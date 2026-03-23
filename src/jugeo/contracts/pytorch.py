"""PyTorch contracts for JuGeo — shape safety and numerical stability.

This module defines library contracts for core PyTorch operations so that
``jugeo prove`` can verify tensor programs without PyTorch source code.

Each contract specifies shape pre/post-conditions that JuGeo translates
into local propositions at call-site coordinates.  When the descent engine
finds H¹ = 0, the entire tensor pipeline is shape-safe.

Usage::

    # Import to register all contracts, then run jugeo prove
    import jugeo.contracts.pytorch  # noqa: F401

    # Or inspect the contracts
    from jugeo.contracts import get_registry
    for name, c in get_registry().all().items():
        print(f"{name}: {c.n_obligations} obligations")
"""

from jugeo.contracts.core import library_contract, requires, ensures, invariant


# ═══════════════════════════════════════════════════════════════════
# 1. Core tensor operations
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.matmul", "Matrix/batch matrix multiplication")
@requires("len(A.shape) >= 1 and len(B.shape) >= 1")
@requires("A.shape[-1] == B.shape[-2] if len(B.shape) >= 2 else A.shape[-1] == B.shape[-1]")
@ensures("result.ndim == max(A.ndim, B.ndim)")
@ensures("result.shape[-1] == (B.shape[-1] if B.ndim >= 2 else 1)")
def matmul(A, B): ...


@library_contract("torch.bmm", "Batched matrix multiplication (3-D only)")
@requires("A.ndim == 3 and B.ndim == 3")
@requires("A.shape[0] == B.shape[0]")
@requires("A.shape[2] == B.shape[1]")
@ensures("result.shape == (A.shape[0], A.shape[1], B.shape[2])")
def bmm(A, B): ...


@library_contract("torch.mm", "2-D matrix multiplication")
@requires("A.ndim == 2 and B.ndim == 2")
@requires("A.shape[1] == B.shape[0]")
@ensures("result.shape == (A.shape[0], B.shape[1])")
def mm(A, B): ...


@library_contract("torch.mv", "Matrix-vector product")
@requires("A.ndim == 2 and v.ndim == 1")
@requires("A.shape[1] == v.shape[0]")
@ensures("result.shape == (A.shape[0],)")
def mv(A, v): ...


@library_contract("torch.dot", "Inner product of 1-D tensors")
@requires("a.ndim == 1 and b.ndim == 1")
@requires("a.shape[0] == b.shape[0]")
@ensures("result.ndim == 0")
def dot(a, b): ...


@library_contract("torch.einsum", "Einstein summation")
@requires("isinstance(equation, str)")
@ensures("result is not None")
def einsum(equation, *operands): ...


# ═══════════════════════════════════════════════════════════════════
# 2. Shape manipulation
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.transpose", "Transpose two dimensions")
@requires("0 <= dim0 < input.ndim and 0 <= dim1 < input.ndim")
@ensures("result.shape[dim0] == input.shape[dim1]")
@ensures("result.shape[dim1] == input.shape[dim0]")
@ensures("result.numel() == input.numel()")
def transpose(input, dim0, dim1): ...


@library_contract("torch.reshape", "Reshape tensor to new shape")
@requires("all(s >= -1 for s in shape)")
@requires("sum(1 for s in shape if s == -1) <= 1")
@ensures("result.numel() == input.numel()")
def reshape(input, shape): ...


@library_contract("torch.view", "View tensor with new shape (contiguous)")
@requires("input.is_contiguous()")
@ensures("result.numel() == input.numel()")
def view(input, *shape): ...


@library_contract("torch.unsqueeze", "Insert size-1 dimension")
@requires("-input.ndim - 1 <= dim <= input.ndim")
@ensures("result.ndim == input.ndim + 1")
@ensures("result.shape[dim] == 1")
def unsqueeze(input, dim): ...


@library_contract("torch.squeeze", "Remove size-1 dimensions")
@ensures("result.numel() == input.numel()")
@ensures("all(s != 1 for s in result.shape) if dim is None else True")
def squeeze(input, dim=None): ...


@library_contract("torch.cat", "Concatenate tensors along a dimension")
@requires("len(tensors) >= 1")
@requires("all(t.ndim == tensors[0].ndim for t in tensors)")
@ensures("result.ndim == tensors[0].ndim")
@ensures("result.shape[dim] == sum(t.shape[dim] for t in tensors)")
def cat(tensors, dim=0): ...


@library_contract("torch.stack", "Stack tensors along a new dimension")
@requires("len(tensors) >= 1")
@requires("all(t.shape == tensors[0].shape for t in tensors)")
@ensures("result.ndim == tensors[0].ndim + 1")
@ensures("result.shape[dim] == len(tensors)")
def stack(tensors, dim=0): ...


@library_contract("torch.flatten", "Flatten dimensions [start, end]")
@requires("0 <= start_dim < input.ndim")
@requires("start_dim <= end_dim < input.ndim")
@ensures("result.numel() == input.numel()")
def flatten(input, start_dim=0, end_dim=-1): ...


@library_contract("torch.permute", "Permute tensor dimensions")
@requires("len(dims) == input.ndim")
@requires("set(dims) == set(range(input.ndim))")
@ensures("result.numel() == input.numel()")
def permute(input, dims): ...


# ═══════════════════════════════════════════════════════════════════
# 3. Activation functions
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.relu", "ReLU activation (element-wise)")
@ensures("result.shape == input.shape")
@ensures("result.dtype == input.dtype")
def relu(input): ...


@library_contract("torch.sigmoid", "Sigmoid activation (element-wise)")
@ensures("result.shape == input.shape")
def sigmoid(input): ...


@library_contract("torch.tanh", "Tanh activation (element-wise)")
@ensures("result.shape == input.shape")
def tanh(input): ...


@library_contract("torch.gelu", "GELU activation (element-wise)")
@ensures("result.shape == input.shape")
def gelu(input): ...


@library_contract("torch.silu", "SiLU/Swish activation (element-wise)")
@ensures("result.shape == input.shape")
def silu(input): ...


@library_contract("torch.leaky_relu", "Leaky ReLU activation")
@requires("negative_slope >= 0")
@ensures("result.shape == input.shape")
def leaky_relu(input, negative_slope=0.01): ...


# ═══════════════════════════════════════════════════════════════════
# 4. Normalization
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.layer_norm", "Layer normalization")
@requires("len(normalized_shape) <= input.ndim")
@requires("input.shape[-len(normalized_shape):] == tuple(normalized_shape)")
@ensures("result.shape == input.shape")
def layer_norm(input, normalized_shape, weight=None, bias=None, eps=1e-5): ...


@library_contract("torch.nn.functional.batch_norm", "Batch normalization")
@requires("input.ndim >= 2")
@ensures("result.shape == input.shape")
def batch_norm(input, running_mean, running_var, weight=None, bias=None,
               training=False, momentum=0.1, eps=1e-5): ...


@library_contract("torch.nn.functional.group_norm", "Group normalization")
@requires("input.ndim >= 2")
@requires("input.shape[1] % num_groups == 0")
@ensures("result.shape == input.shape")
def group_norm(input, num_groups, weight=None, bias=None, eps=1e-5): ...


# ═══════════════════════════════════════════════════════════════════
# 5. Softmax and log operations
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.softmax", "Softmax along dimension")
@requires("0 <= dim < input.ndim or (-input.ndim <= dim < 0)")
@ensures("result.shape == input.shape")
def softmax(input, dim): ...


@library_contract("torch.log_softmax", "Log-softmax along dimension")
@requires("0 <= dim < input.ndim or (-input.ndim <= dim < 0)")
@ensures("result.shape == input.shape")
def log_softmax(input, dim): ...


# ═══════════════════════════════════════════════════════════════════
# 6. Convolution
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.conv1d", "1D convolution")
@requires("input.ndim == 3")
@requires("weight.ndim == 3")
@requires("input.shape[1] == weight.shape[1] * groups")
@ensures("result.ndim == 3")
@ensures("result.shape[0] == input.shape[0]")
@ensures("result.shape[1] == weight.shape[0]")
def conv1d(input, weight, bias=None, stride=1, padding=0, dilation=1, groups=1): ...


@library_contract("torch.nn.functional.conv2d", "2D convolution")
@requires("input.ndim == 4")
@requires("weight.ndim == 4")
@requires("input.shape[1] == weight.shape[1] * groups")
@ensures("result.ndim == 4")
@ensures("result.shape[0] == input.shape[0]")
@ensures("result.shape[1] == weight.shape[0]")
def conv2d(input, weight, bias=None, stride=1, padding=0, dilation=1, groups=1): ...


# ═══════════════════════════════════════════════════════════════════
# 7. Pooling
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.max_pool2d", "2D max pooling")
@requires("input.ndim == 4")
@ensures("result.ndim == 4")
@ensures("result.shape[0] == input.shape[0]")
@ensures("result.shape[1] == input.shape[1]")
def max_pool2d(input, kernel_size, stride=None, padding=0, dilation=1): ...


@library_contract("torch.nn.functional.avg_pool2d", "2D average pooling")
@requires("input.ndim == 4")
@ensures("result.ndim == 4")
@ensures("result.shape[0] == input.shape[0]")
@ensures("result.shape[1] == input.shape[1]")
def avg_pool2d(input, kernel_size, stride=None, padding=0): ...


@library_contract("torch.nn.functional.adaptive_avg_pool2d", "Adaptive avg pool")
@requires("input.ndim == 4")
@ensures("result.ndim == 4")
@ensures("result.shape[0] == input.shape[0]")
@ensures("result.shape[1] == input.shape[1]")
@ensures("result.shape[2] == output_size[0] and result.shape[3] == output_size[1]")
def adaptive_avg_pool2d(input, output_size): ...


# ═══════════════════════════════════════════════════════════════════
# 8. Loss functions
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.cross_entropy", "Cross-entropy loss")
@requires("input.ndim >= 2")
@requires("target.shape[0] == input.shape[0]")
@ensures("result.ndim == 0 if reduction == 'mean' else result.ndim >= 0")
def cross_entropy(input, target, weight=None, reduction='mean'): ...


@library_contract("torch.nn.functional.mse_loss", "Mean squared error loss")
@requires("input.shape == target.shape")
@ensures("result.ndim == 0 if reduction == 'mean' else result.shape == input.shape")
def mse_loss(input, target, reduction='mean'): ...


@library_contract("torch.nn.functional.binary_cross_entropy_with_logits", "BCE with logits")
@requires("input.shape == target.shape")
@ensures("result.ndim == 0 if reduction == 'mean' else result.shape == input.shape")
def binary_cross_entropy_with_logits(input, target, weight=None, reduction='mean'): ...


# ═══════════════════════════════════════════════════════════════════
# 9. Dropout
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.dropout", "Dropout regularization")
@requires("0 <= p < 1")
@ensures("result.shape == input.shape")
def dropout(input, p=0.5, training=True, inplace=False): ...


# ═══════════════════════════════════════════════════════════════════
# 10. Embedding
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.embedding", "Embedding lookup")
@requires("weight.ndim == 2")
@requires("input.max() < weight.shape[0]")
@requires("input.min() >= 0")
@ensures("result.shape == (*input.shape, weight.shape[1])")
def embedding(input, weight, padding_idx=None): ...


# ═══════════════════════════════════════════════════════════════════
# 11. Attention (Transformer)
# ═══════════════════════════════════════════════════════════════════

@library_contract("torch.nn.functional.scaled_dot_product_attention",
                   "Scaled dot-product attention (PyTorch 2.0+)")
@requires("query.ndim >= 3 and key.ndim >= 3 and value.ndim >= 3")
@requires("query.shape[-1] == key.shape[-1]")
@requires("key.shape[-2] == value.shape[-2]")
@ensures("result.shape[:-1] == query.shape[:-1]")
@ensures("result.shape[-1] == value.shape[-1]")
def scaled_dot_product_attention(query, key, value, attn_mask=None,
                                  dropout_p=0.0, is_causal=False): ...


# ═══════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════

def _count() -> int:
    from jugeo.contracts.core import ContractRegistry
    return len(ContractRegistry.all())


if __name__ == "__main__":
    from jugeo.contracts import get_registry
    print(get_registry().summary())
