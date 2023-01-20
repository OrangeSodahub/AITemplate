#  Copyright (c) Meta Platforms, Inc. and affiliates.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
Codegen for conv2d_depthwise.
"""
import jinja2

from ... import registry
from . import common, conv2d_depthwise as cdw

# pylint: disable=C0103,C0415,W0613,C0301

EXEC_TEMPLATE = jinja2.Template(
    """
{{indent}}using ElementComputeEpilogue = typename {{instance}}::ElementCompute;
//  TODO: cast to right dtype
{{indent}}typename {{instance}}::Arguments arguments{
{{indent}}    problem_size,
{{indent}}    {static_cast<{{dtype}}*>(in_ptr), layout_A},
{{indent}}    {static_cast<{{dtype}}*>(weight_ptr), layout_B},
{{indent}}    {static_cast<{{dtype}}*>(bias_ptr), cutlass::layout::TensorNHWC::Stride(0)},
{{indent}}    {static_cast<{{dtype}}*>(out_ptr), layout_C},
{{indent}}    {ElementComputeEpilogue(1), ElementComputeEpilogue(1)},
{{indent}}};
{{indent}}{{instance}} implicit_gemm_op;
{% if is_profiler %}
{{indent}}size_t workspace_size = implicit_gemm_op.get_workspace_size(arguments);
{{indent}}cutlass::device_memory::allocation<uint8_t> local_workspace(workspace_size);
{{indent}}workspace = local_workspace.get();
{{indent}}GLOBAL_WORKSPACE_SIZE = workspace_size;
{% endif %}
{{indent}}auto status = implicit_gemm_op.can_implement(arguments);
{{indent}}CUTLASS_CHECK(status);
{{indent}}status = implicit_gemm_op.initialize(arguments, workspace);
{{indent}}CUTLASS_CHECK(status);
{{indent}}status = implicit_gemm_op(stream);
{{indent}}CUTLASS_CHECK(status);
{{indent}}return;
"""
)

SRC_TEMPLATE = jinja2.Template(
    """
#include <iostream>
#include <string>
#include <stdexcept>
#include "cutlass/cutlass.h"
#include "cutlass/conv/kernel/default_depthwise_fprop.h"
#include "cutlass/conv/device/implicit_gemm_convolution.h"
#include "cutlass/util/host_tensor.h"
#include "cutlass/util/reference/host/tensor_fill.h"

{{extra_header}}

#define CUTLASS_CHECK(status)                                                         \\
  {                                                                                   \\
    cutlass::Status error = status;                                                   \\
    if (error != cutlass::Status::kSuccess) {                                         \\
      auto msg = std::string("[") + __FILE__ + "] Got cutlass error: " +              \\
          cutlassGetStatusString(error) + " at: " + std::to_string(__LINE__);         \\
      std::cerr << msg << std::endl;                                                  \\
      throw std::runtime_error(msg);                                                  \\
    }                                                                                 \\
  }

{{instances}}

{{instances_def}}

void {{function_name}} (
    void* in_ptr,
    void* weight_ptr,
    void* out_ptr,
    void* bias_ptr,
    uint8_t* workspace,
    int64_t* batch,
    int64_t* out_ch,
    int64_t* in_ch,
    int64_t* kernel_h,
    int64_t* kernel_w,
    int64_t* in_h,
    int64_t* in_w,
    int64_t* out_batch,
    int64_t* out_h,
    int64_t* out_w,
    int stride,
    int dilation,
    int pad,
    cudaStream_t stream
  ) {

  {{shape_function}}
  int i32_batch = *batch;
  int i32_in_h = *in_h;
  int i32_in_w = *in_w;
  int i32_in_ch = *in_ch;
  int i32_out_ch = *out_ch;
  int i32_kernel_h = *kernel_h;
  int i32_kernel_w = *kernel_w;
  int i32_out_batch = *out_batch;
  int i32_out_h = *out_h;
  int i32_out_w = *out_w;

  using cutlass::layout::TensorNHWC;
  TensorNHWC layout_A(TensorNHWC::packed(cutlass::make_Coord(i32_batch, i32_in_h, i32_in_w, i32_in_ch)));
  TensorNHWC layout_B(TensorNHWC::packed(cutlass::make_Coord(i32_out_ch, i32_kernel_h, i32_kernel_w, 1)));
  TensorNHWC layout_C(TensorNHWC::packed(cutlass::make_Coord(i32_out_batch, i32_out_h, i32_out_w, i32_out_ch)));

  cutlass::conv::Conv2dProblemSize problem_size(
    {i32_batch, i32_in_h, i32_in_w, i32_in_ch},
        {i32_out_ch, i32_kernel_h, i32_kernel_w, 1},
        {pad, pad, pad, pad},
        {stride, stride},
        {dilation, dilation},
        {i32_out_batch, i32_out_h, i32_out_w, i32_out_ch},
        cutlass::conv::Mode::kCrossCorrelation,
        1,
        i32_in_ch
  );

  {{exec_paths}}
  throw std::runtime_error(
      "Unsupported workload for this conv2d specialization."
  );
}
"""
)


PROFILER_TEMPLATE = jinja2.Template(
    """
size_t GLOBAL_WORKSPACE_SIZE = 0;

{{op_func}}

int main(int argc, char** argv) {
  int64_t batch = std::stoi(argv[1]);
  int64_t in_h = std::stoi(argv[2]);
  int64_t in_w = std::stoi(argv[3]);
  int64_t in_ch = std::stoi(argv[4]);
  int64_t kernel_h = std::stoi(argv[5]);
  int64_t kernel_w = std::stoi(argv[6]);
  int64_t out_ch = std::stoi(argv[7]);
  int stride = std::stoi(argv[8]);
  int pad = std::stoi(argv[9]);
  int dilation = std::stoi(argv[10]);
  {{shape_func}}
  using ElementOutput = typename {{name}}::ElementC;
  using ElementInputA = typename {{name}}::ElementA;
  using ElementInputB = typename {{name}}::ElementB;

  uint8_t* global_workspace = nullptr;
  cudaStream_t stream = nullptr;

  cutlass::HostTensor<ElementInputA, typename {{name}}::LayoutA> x({NI, HI, WI, CI});
  cutlass::HostTensor<ElementInputB, typename {{name}}::LayoutB> w({CO, KH, KW, 1u});
  cutlass::HostTensor<ElementInputB, typename {{name}}::LayoutB> b({(int)CO, 1, 1, 1});
  cutlass::HostTensor<ElementOutput, typename {{name}}::LayoutC> y({NO, HO, WO, CO});

  //
  // warmup
  conv(x.device_data(),
       w.device_data(),
       y.device_data(),
       b.device_data(),
       global_workspace,
       &NI,
       &CO,
       &CI,
       &KH,
       &KW,
       &HI,
       &WI,
       &NO,
       &HO,
       &WO,
       stride,
       dilation,
       pad,
       stream);
  cudaEvent_t events[2];
  for (auto & event : events) {
    cudaEventCreate(&event);
  }
  cudaEventRecord(events[0], stream);
  for (int i = 0; i < 5; ++i) {
      conv(x.device_data(),
       w.device_data(),
       y.device_data(),
       b.device_data(),
       global_workspace,
       &NI,
       &CO,
       &CI,
       &KH,
       &KW,
       &HI,
       &WI,
       &NO,
       &HO,
       &WO,
       stride,
       dilation,
       pad,
       stream);
  }
  cudaEventRecord(events[1], stream);
  cudaEventSynchronize(events[1]);
  float runtime_ms = 0;
  cudaEventElapsedTime(&runtime_ms, events[0], events[1]);
  for (auto event : events) {
    (void)cudaEventDestroy(event);
  }
  // TODO: output workspace
  if (runtime_ms < 0.00001) {
      throw std::runtime_error(
      "OOB in cutlass."
    );
  }
  std::cout << "TIME:" << runtime_ms << std::endl;
  std::cout << "WS:" << GLOBAL_WORKSPACE_SIZE << std::endl;
}

"""
)

FUNC_DECL_TEMPLATE = jinja2.Template(
    """
void {{func_name}}(
  void*,
  void*,
  void*,
  void*,
  uint8_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int64_t*,
  int,
  int,
  int,
  cudaStream_t
);
"""
)

FUNC_CALL_TEMPLATE = jinja2.Template(
    """
{{indent}}{{func_name}}(
{{indent}}    {{in_ptr}},
{{indent}}    {{weight_ptr}},
{{indent}}    {{out_ptr}},
{{indent}}    {{bias_ptr}},
{{indent}}    global_workspace_,
{{indent}}    {{p_batch}},
{{indent}}    {{p_out_ch}},
{{indent}}    {{p_in_ch}},
{{indent}}    {{p_kernel_h}},
{{indent}}    {{p_kernel_w}},
{{indent}}    {{p_in_h}},
{{indent}}    {{p_in_w}},
{{indent}}    {{p_out_batch}},
{{indent}}    {{p_out_h}},
{{indent}}    {{p_out_w}},
{{indent}}    {{stride}},
{{indent}}    {{dilation}},
{{indent}}    {{pad}},
{{indent}}    stream
{{indent}});
"""
)


@registry.reg("cuda.conv2d_depthwise_bias.config")
def conv2d_depthwise_config(func_attrs, dtype="float16"):
    """Populates conv2d_depthwise cutlass configs into 'op_instance' field."""
    func_attrs["op_instance"] = cdw.extract_config(func_attrs)


@registry.reg("cuda.conv2d_depthwise_bias.gen_profiler")
def gen_profiler(func_attrs, workdir, shape_template):
    """Codegen for conv2d_depthwise_bias profiler."""
    return cdw.gen_profiler(
        func_attrs,
        workdir,
        shape_template,
        exec_template=EXEC_TEMPLATE,
        src_template=SRC_TEMPLATE,
        profiler_template=PROFILER_TEMPLATE,
    )


@registry.reg("cuda.conv2d_depthwise_bias.gen_function")
def gen_function(
    func_attrs,
    exec_cond_remplate,
    shape_eval_template,
    shape_save_template,
):
    """Codegen for conv2d_depthwise_bias function."""
    return common.gen_function(
        func_attrs,
        cdw.INSTANCE_TEMPLATE,
        EXEC_TEMPLATE,
        SRC_TEMPLATE,
        exec_cond_remplate,
        shape_eval_template,
        shape_save_template,
        f_emit_instance=cdw.emit_instance,
    )


@registry.reg("cuda.conv2d_depthwise_bias.func_decl")
def conv2d_depthwise_gen_function_decl(func_attrs):
    """Codegen for conv2d_depthwise_bias function declaration."""
    func_name = func_attrs["name"]
    return FUNC_DECL_TEMPLATE.render(func_name=func_name)


@registry.reg("cuda.conv2d_depthwise_bias.func_call")
def conv2d_depthwise_gen_function_call(func_attrs, indent="  "):
    """Codegen for conv2d_depthwise_bias function call."""
    x = func_attrs["inputs"][0]
    xshape = x._attrs["shape"]
    w = func_attrs["inputs"][1]
    b = func_attrs["inputs"][2]
    wshape = w._attrs["shape"]
    y = func_attrs["outputs"][0]
    yshape = y._attrs["shape"]
    return FUNC_CALL_TEMPLATE.render(
        func_name=func_attrs["name"],
        in_ptr=x._attrs["name"],
        weight_ptr=w._attrs["name"],
        out_ptr=y._attrs["name"],
        bias_ptr=b._attrs["name"],
        p_batch="&" + xshape[0]._attrs["name"],
        p_out_ch="&" + wshape[0]._attrs["name"],
        p_in_ch="&" + xshape[3]._attrs["name"],
        p_kernel_h="&" + wshape[1]._attrs["name"],
        p_kernel_w="&" + wshape[2]._attrs["name"],
        p_in_h="&" + xshape[1]._attrs["name"],
        p_in_w="&" + xshape[2]._attrs["name"],
        p_out_batch="&" + yshape[0]._attrs["name"],
        p_out_h="&" + yshape[1]._attrs["name"],
        p_out_w="&" + yshape[2]._attrs["name"],
        stride=func_attrs["stride"],
        dilation=func_attrs["dilate"],
        pad=func_attrs["pad"],
        indent=indent,
    )


@registry.reg("cuda.conv2d_depthwise_bias.filter")
def conv2d_depthwise_bias_function_filter(cfg, func_attrs, x_shape):
    """Generates function filter.

    Parameters
    ----------
    cfg: str
        The filename generated for profiler.
    func_attrs : Dict
        Stores the operation attributes.
    x_shape:
        Input shapes.

    Returns
    -------
    bool
        If input cfg should be filtered.
    """
    return True
