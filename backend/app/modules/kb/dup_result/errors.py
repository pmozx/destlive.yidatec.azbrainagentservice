"""重复治理领域异常：把「403 / 409 / 404」的语义留在领域层，HTTP 码由契约层映射。

命名与契约错误码一一对应（§九）：
``DupScopeDeniedError`` → 403 ``GOVERNANCE_SCOPE_DENIED``；
``DupSourceChangedError`` → 409 ``SOURCE_CHANGED``；
``DupResultExpiredError`` → 409 ``RESULT_EXPIRED``；
``DupResultNotFoundError`` → 404 ``NOT_FOUND``；
``DupInvalidRequestError`` → 400 ``INVALID_REQUEST``。

领域层不 import ``modules_api``：那会让基座依赖契约层，违反依赖方向（规范 R6）。
"""


class DupScopeDeniedError(Exception):
    """治理范围与本地归属不一致（租户 / 资源类型 / 资源 ID）。

    不降级放行：把别人的发布单元当成本次范围参与比较，等于越权读取内容。
    """


class DupSourceChangedError(Exception):
    """来源内容与 scope 声明不一致：快照被推进、正文摘要变了，或本地尚未同步。

    这类失败必须显式报错，绝不能返回「未发现重复」——调用方会据此认为可以保存。
    """


class DupResultNotFoundError(Exception):
    """结果或分组不存在 / 不属于当前租户与用户。

    租户或用户不匹配时同样按「不存在」处理：契约允许用 404 隐藏他人检测结果的存在。
    """


class DupResultExpiredError(Exception):
    """只读结果快照已过保留期（30 分钟），必须重新发起检测。"""


class DupInvalidRequestError(Exception):
    """请求与结果状态自相矛盾：主项不在分组内、处置动作不完整、切片不允许合并答案等。"""
