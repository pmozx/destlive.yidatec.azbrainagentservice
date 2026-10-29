import json
from typing import Union, Any, Optional

from rich.console import Console
from rich.pretty import Pretty
from rich.markdown import Markdown
from rich.syntax import Syntax

# ============================================
# Rich 美化功能
# ============================================
# 全局 Console 实例
_console = Console(width=120, color_system=None)  # 无颜色，适合日志文件
_console_color = Console(width=120, color_system="auto")  # 有颜色，适合终端


# ---------- 1. 数据美化（Pretty） ----------
def pretty_log(data, indent_guides=False, expand_all=True):
    """
    使用 Rich 美化数据（无颜色，适合日志文件）

    Args:
        data: 要格式化的数据
        indent_guides: 是否显示缩进引导线
        expand_all: 是否强制展开为多行

    Returns:
        str: 美化后的字符串

    Examples:
        >>> logger.info(f"人员信息: \\n{pretty_log(user_data)}")
    """
    with _console.capture() as capture:
        _console.print(
            Pretty(data, indent_guides=indent_guides, expand_all=expand_all)
        )
    return capture.get()


def pretty_log_color(data, indent_guides=False, expand_all=True):
    """
    使用 Rich 美化数据（带颜色，适合终端）

    Returns:
        str: 带颜色代码的美化字符串
    """
    with _console_color.capture() as capture:
        _console_color.print(
            Pretty(data, indent_guides=indent_guides, expand_all=expand_all)
        )
    return capture.get()


def pretty_print(data, indent_guides=False, expand_all=True):
    """
    直接打印美化数据到终端（带颜色）

    Examples:
        >>> pp(emp_data.address)
        >>> pp(emp_data, indent_guides=False)
    """
    _console_color.print(
        Pretty(data, indent_guides=indent_guides, expand_all=expand_all)
    )


# ---------- 2. Markdown 美化 ----------
def markdown_log(content, code_theme="default", inline_code_theme="default"):
    """
    使用 Rich 渲染 Markdown（无颜色，适合日志文件）

    Args:
        content: Markdown 格式的字符串
        code_theme: 代码块主题（可选：'default', 'monokai', 'github-dark' 等）
        inline_code_theme: 行内代码主题

    Returns:
        str: 渲染后的字符串

    Examples:
        >>> md_content = \"\"\"
        ... # 标题
        ...
        ... - 列表项1
        ... - 列表项2
        ...
        ... **粗体** 和 *斜体*
        ... \"\"\"
        >>> logger.info(f"Markdown: \\n{markdown_log(md_content)}")
    """
    with _console.capture() as capture:
        _console.print(
            Markdown(
                content,
                code_theme=code_theme,
                inline_code_theme=inline_code_theme
            )
        )
    return capture.get()


def markdown_log_color(content, code_theme="default", inline_code_theme="default"):
    """
    使用 Rich 渲染 Markdown（带颜色，适合终端）

    Returns:
        str: 带颜色代码的渲染字符串
    """
    with _console_color.capture() as capture:
        _console_color.print(
            Markdown(
                content,
                code_theme=code_theme,
                inline_code_theme=inline_code_theme
            )
        )
    return capture.get()


def markdown_print(content, code_theme="default", inline_code_theme="default"):
    """
    直接打印 Markdown 到终端（带颜色）

    Examples:
        >>> md(\"\"\"
        ... # 测试报告
        ...
        ... ## 功能测试
        ... - ✅ 登录功能正常
        ... - ✅ 数据查询正常
        ... \"\"\")
    """
    _console_color.print(
        Markdown(
            content,
            code_theme=code_theme,
            inline_code_theme=inline_code_theme
        )
    )


# ---------- 2. JSON 专用函数 ----------
def _parse_json_input(data: Union[str, dict, list, Any]) -> Any:
    """
    解析 JSON 输入，支持字符串或对象

    Args:
        data: JSON 字符串或 JSON 对象（dict/list/其他可序列化类型）

    Returns:
        解析后的 JSON 对象

    Raises:
        ValueError: 如果字符串不是有效的 JSON 格式
        TypeError: 如果数据类型不支持
    """
    if isinstance(data, str):
        # 去除首尾空白字符
        data = data.strip()
        if not data:
            raise ValueError("JSON 字符串不能为空")
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            # 提供更友好的错误信息
            error_pos = e.pos if hasattr(e, 'pos') else 0
            error_line = e.lineno if hasattr(e, 'lineno') else 0
            error_col = e.colno if hasattr(e, 'colno') else 0
            raise ValueError(
                f"无效的 JSON 字符串 (行 {error_line}, 列 {error_col}): {e.msg}\n"
                f"问题位置: {data[max(0, error_pos - 20):error_pos + 20]}"
            )
    elif isinstance(data, (dict, list, int, float, bool)) or data is None:
        return data
    else:
        raise TypeError(
            f"data 必须是 JSON 字符串、dict、list 或基本类型，"
            f"当前类型: {type(data).__name__}"
        )


def _format_json(data: Any,
                 indent: Optional[int] = 2,
                 ensure_ascii: bool = False,
                 sort_keys: bool = False) -> str:
    """
    内部函数：格式化 JSON 数据

    Returns:
        格式化后的 JSON 字符串
    """
    # 解析输入
    json_obj = _parse_json_input(data)

    # 格式化 JSON
    indent_val = indent if indent is not None else None
    try:
        json_str = json.dumps(
            json_obj,
            ensure_ascii=ensure_ascii,
            indent=indent_val,
            sort_keys=sort_keys,
            separators=(',', ': ') if indent_val is not None else (',', ':')
        )
        return json_str
    except TypeError as e:
        raise TypeError(f"数据无法序列化为 JSON: {e}")


def json_log(data: Union[str, dict, list, Any],
             indent: Optional[int] = 2,
             ensure_ascii: bool = False,
             sort_keys: bool = False) -> str:
    """
    将数据格式化为 JSON 字符串（无颜色，适合日志文件）

    Args:
        data: JSON 字符串或 JSON 对象（dict/list/其他可序列化类型）
        indent: 缩进空格数，设为 None 则输出紧凑格式（默认 2）
        ensure_ascii: 是否确保 ASCII（False 显示中文，默认 False）
        sort_keys: 是否按键排序（默认 False）

    Returns:
        str: 格式化的 JSON 字符串

    Examples:
        >>> # JSON 对象
        >>> user_data = {"name": "张三", "age": 30, "hobbies": ["读书", "游泳"]}
        >>> logger.info(f"用户数据:\\n{json_log(user_data)}")

        >>> # JSON 字符串
        >>> json_str = '{"name": "李四", "age": 25}'
        >>> logger.info(f"JSON数据:\\n{json_log(json_str)}")

        >>> # 紧凑格式
        >>> logger.info(f"紧凑JSON: {json_log(user_data, indent=None)}")

        >>> # 排序输出
        >>> logger.info(f"排序JSON:\\n{json_log(user_data, sort_keys=True)}")
    """
    try:
        json_str = _format_json(data, indent, ensure_ascii, sort_keys)
        with _console.capture() as capture:
            _console.print(json_str)
        return capture.get()
    except (ValueError, TypeError) as e:
        # 返回错误信息，不影响日志记录
        error_msg = f"[JSON 格式化错误] {e}"
        with _console.capture() as capture:
            _console.print(error_msg)
        return capture.get()


def json_log_color(data: Union[str, dict, list, Any],
                   indent: Optional[int] = 2,
                   ensure_ascii: bool = False,
                   sort_keys: bool = False,
                   theme: str = "monokai") -> str:
    """
    将数据格式化为带语法高亮的 JSON（带颜色，适合终端）

    Args:
        data: JSON 字符串或 JSON 对象（dict/list/其他可序列化类型）
        indent: 缩进空格数，设为 None 则输出紧凑格式（默认 2）
        ensure_ascii: 是否确保 ASCII（False 显示中文，默认 False）
        sort_keys: 是否按键排序（默认 False）
        theme: 语法高亮主题（monokai, github-dark, default, nord 等）

    Returns:
        str: 带颜色代码的 JSON 字符串

    Examples:
        >>> data = {"name": "张三", "age": 30}
        >>> colored = json_log_color(data, theme="monokai")
        >>> logger.info(f"彩色JSON:\\n{colored}")

        >>> # 带中文显示
        >>> logger.info(f"中文JSON:\\n{json_log_color(data, ensure_ascii=False)}")
    """
    try:
        json_str = _format_json(data, indent, ensure_ascii, sort_keys)
        with _console_color.capture() as capture:
            _console_color.print(
                Syntax(json_str, "json", theme=theme, word_wrap=True)
            )
        return capture.get()
    except (ValueError, TypeError) as e:
        # 返回错误信息（带颜色）
        error_msg = f"[bold red]JSON 格式化错误:[/bold red] {e}"
        with _console_color.capture() as capture:
            _console_color.print(error_msg)
        return capture.get()


def json_print(data: Union[str, dict, list, Any],
               indent: Optional[int] = 2,
               ensure_ascii: bool = False,
               sort_keys: bool = False,
               theme: str = "monokai") -> None:
    """
    直接打印格式化的 JSON 到终端（带颜色和语法高亮）

    Args:
        data: JSON 字符串或 JSON 对象（dict/list/其他可序列化类型）
        indent: 缩进空格数，设为 None 则输出紧凑格式（默认 2）
        ensure_ascii: 是否确保 ASCII（False 显示中文，默认 False）
        sort_keys: 是否按键排序（默认 False）
        theme: 语法高亮主题（monokai, github-dark, default, nord 等）

    Examples:
        >>> # 打印字典
        >>> data = {
        ...     "name": "张三",
        ...     "age": 30,
        ...     "hobbies": ["读书", "游泳", "编程"],
        ...     "address": {"city": "北京", "district": "朝阳区"}
        ... }
        >>> json_print(data)

        >>> # 打印 JSON 字符串
        >>> json_print('{"name": "李四", "age": 25}')

        >>> # 自定义格式
        >>> json_print(data, indent=4, sort_keys=True, theme="github-dark")

        >>> # 紧凑格式
        >>> json_print(data, indent=None)
    """
    try:
        json_str = _format_json(data, indent, ensure_ascii, sort_keys)
        _console_color.print(
            Syntax(json_str, "json", theme=theme, word_wrap=True)
        )
    except (ValueError, TypeError) as e:
        # 打印错误信息（红色显示）
        _console_color.print(f"[bold red]JSON 格式化错误:[/bold red] {e}")


# ---------- 3. JSON 文件操作（新增） ----------
def json_file_log(file_path: str,
                  indent: Optional[int] = 2,
                  ensure_ascii: bool = False,
                  sort_keys: bool = False) -> str:
    """
    加载 JSON 文件并美化输出（无颜色，适合日志文件）

    Args:
        file_path: JSON 文件路径
        indent: 缩进空格数（默认 2）
        ensure_ascii: 是否确保 ASCII（默认 False）
        sort_keys: 是否按键排序（默认 False）

    Returns:
        str: 美化后的 JSON 字符串

    Examples:
        >>> logger.info(f"配置文件:\\n{json_file_log('config.json')}")
        >>> logger.info(f"配置详情:\\n{json_file_log('config.json', indent=4)}")
    """
    try:
        import os
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return json_log(data, indent=indent, ensure_ascii=ensure_ascii,
                        sort_keys=sort_keys)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        error_msg = f"[文件加载错误] {e}"
        with _console.capture() as capture:
            _console.print(error_msg)
        return capture.get()


def json_file_print(file_path: str,
                    indent: Optional[int] = 2,
                    ensure_ascii: bool = False,
                    sort_keys: bool = False,
                    theme: str = "monokai") -> None:
    """
    加载 JSON 文件并直接打印到终端（带颜色）

    Args:
        file_path: JSON 文件路径
        indent: 缩进空格数（默认 2）
        ensure_ascii: 是否确保 ASCII（默认 False）
        sort_keys: 是否按键排序（默认 False）
        theme: 语法高亮主题（默认 monokai）

    Examples:
        >>> json_file_print('config.json')
        >>> json_file_print('config.json', indent=4, theme="github-dark")
    """
    try:
        import os
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        json_print(data, indent=indent, ensure_ascii=ensure_ascii,
                   sort_keys=sort_keys, theme=theme)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        _console_color.print(f"[bold red]JSON 文件加载错误:[/bold red] {e}")


def json_is_valid(data: Union[str, dict, list, Any]) -> bool:
    """
    检查数据是否为有效的 JSON

    Args:
        data: 要检查的数据

    Returns:
        bool: 是否为有效的 JSON

    Examples:
        >>> json_is_valid('{"name": "test"}')  # True
        >>> json_is_valid('{"name": test}')    # False
        >>> json_is_valid({"name": "test"})    # True
    """
    try:
        _parse_json_input(data)
        return True
    except (ValueError, TypeError):
        return False


def json_minify(data: Union[str, dict, list, Any]) -> str:
    """
    将 JSON 数据压缩为一行（最小化格式）

    Args:
        data: JSON 字符串或 JSON 对象

    Returns:
        str: 压缩后的 JSON 字符串

    Examples:
        >>> data = {"name": "张三", "age": 30}
        >>> json_minify(data)  # '{"name":"张三","age":30}'
    """
    try:
        json_obj = _parse_json_input(data)
        return json.dumps(json_obj, ensure_ascii=False, separators=(',', ':'))
    except (ValueError, TypeError) as e:
        return f'{{"error": "{str(e)}"}}'
# ---------- 4. 通用渲染（自动识别类型） ----------
def render(data, indent_guides=False, expand_all=True, code_theme="default"):
    """
    智能渲染：根据数据类型自动选择 Pretty 或 Markdown

    Args:
        data: 要渲染的数据（字符串、对象、Markdown 内容等）
        indent_guides: 是否显示缩进引导线（仅对 Pretty 有效）
        expand_all: 是否强制展开为多行（仅对 Pretty 有效）
        code_theme: Markdown 代码主题

    Returns:
        str: 渲染后的字符串（无颜色）
    """
    # 如果是字符串，尝试作为 Markdown 渲染
    if isinstance(data, str) and ("\n" in data or "#" in data or "- " in data):
        return markdown_log(data, code_theme=code_theme)
    else:
        return pretty_log(data, indent_guides=indent_guides, expand_all=expand_all)


def render_color(data, indent_guides=False, expand_all=True, code_theme="default"):
    """
    智能渲染（带颜色，适合终端）
    """
    if isinstance(data, str) and ("\n" in data or "#" in data or "- " in data):
        return markdown_log_color(data, code_theme=code_theme)
    else:
        return pretty_log_color(data, indent_guides=indent_guides, expand_all=expand_all)