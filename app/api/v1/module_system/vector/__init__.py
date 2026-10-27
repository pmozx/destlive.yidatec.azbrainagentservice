"""向量存储模块：pgvector 切块的写入、相似检索、按文档删除与自检接口。

embedding 由业务侧生成后传入（本模块不绑定模型供应商），向量读写细节见
``app/core/vector_store.py``。
"""
