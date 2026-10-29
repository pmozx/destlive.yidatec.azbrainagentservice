# -*- coding: utf-8 -*-
"""两份独立开发计划 Excel 的共享渲染工具。

被 gen_kb_plan_xlsx.py（知识平台）与 gen_qc_plan_xlsx.py（语音质检）引用，
保证两份表的样式、口径、校验逻辑完全一致。
"""

from collections import OrderedDict
from datetime import date, timedelta

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------- 样式常量
FONT_NAME = "微软雅黑"
C_TITLE = "1F3864"
C_HEAD = "2F5597"
C_SUB = "D9E2F3"
C_BORDER = "B4C6E7"
C_WARN = "C00000"

F_TITLE = Font(name=FONT_NAME, size=15, bold=True, color="FFFFFF")
F_HEAD = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
F_BODY = Font(name=FONT_NAME, size=9)
F_BOLD = Font(name=FONT_NAME, size=9, bold=True)
F_NOTE = Font(name=FONT_NAME, size=9, color="595959")
F_MS = Font(name=FONT_NAME, size=11, bold=True, color=C_WARN)

FILL_TITLE = PatternFill("solid", fgColor=C_TITLE)
FILL_HEAD = PatternFill("solid", fgColor=C_HEAD)
FILL_SUB = PatternFill("solid", fgColor=C_SUB)
FILL_TOTAL = PatternFill("solid", fgColor="E2EFDA")
FILL_MS = PatternFill("solid", fgColor="FFF2CC")

THIN = Side(style="thin", color=C_BORDER)
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


# ---------------------------------------------------------------- 日期工具
def workdays(start, end, holidays):
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur not in holidays:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def parse_md(text):
    """'11-02' → 2026-11-02；'01-25' → 2027-01-25（跨年计划专用）。"""
    month, day = (int(x) for x in text.split("-"))
    year = 2026 if month >= 11 else 2027
    return date(year, month, day)


def week_of(one, start):
    return (one - start).days // 7


def group_weeks(days, start):
    week_days = OrderedDict()
    for one in days:
        week_days.setdefault(week_of(one, start), []).append(one)
    return week_days


def build_tasks(raw):
    rows = []
    for tid, phase, name, desc, deliver, days, start, end, prio in raw:
        rows.append(
            {
                "id": tid,
                "phase": phase,
                "name": name,
                "desc": desc,
                "deliver": deliver,
                "days": float(days),
                "start": parse_md(start),
                "end": parse_md(end),
                "prio": prio,
            }
        )
    return rows


def validate(tasks, days, start, expect_count, expect_days):
    assert len(tasks) == expect_count, f"任务数应为 {expect_count}，实际 {len(tasks)}"
    total = sum(t["days"] for t in tasks)
    assert abs(total - expect_days) < 1e-9, f"任务人日合计应为 {expect_days}，实际 {total}"
    day_set = set(days)
    used = {}
    for t in tasks:
        assert t["start"] <= t["end"], f"{t['id']} 起止日期非法"
        span = [t["start"] + timedelta(days=i) for i in range((t["end"] - t["start"]).days + 1)]
        span = [x for x in span if x in day_set]
        assert span, f"{t['id']} 未落在任何工作日"
        for one in span:
            assert one not in used, f"{one} 同时被 {used.get(one)} 与 {t['id']} 占用（单人串行校验失败）"
            used[one] = t["id"]
        weeks = {week_of(x, start) for x in span}
        assert len(weeks) == 1, f"{t['id']} 跨周，周负载口径会失真"


def workload_of(tasks, start):
    load = {}
    for t in tasks:
        wk = week_of(t["start"], start)
        load[wk] = load.get(wk, 0.0) + t["days"]
    return load


# ---------------------------------------------------------------- 渲染工具
def put_title(ws, text, ncols, height=26, size=15):
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=text)
    c.font = Font(name=FONT_NAME, size=size, bold=True, color="FFFFFF")
    c.fill = FILL_TITLE
    c.alignment = CENTER
    ws.row_dimensions[1].height = height


def put_header(ws, row, headers, widths=None, start_col=1):
    for idx, h in enumerate(headers, start=start_col):
        c = ws.cell(row=row, column=idx, value=h)
        c.font = F_HEAD
        c.fill = FILL_HEAD
        c.alignment = CENTER
        c.border = BORDER
        if widths:
            ws.column_dimensions[get_column_letter(idx)].width = widths[idx - start_col]


def put_rows(ws, start_row, rows, center_cols=()):
    row = start_row
    for vals in rows:
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_BODY
            c.border = BORDER
            c.alignment = CENTER if idx in center_cols else LEFT
        row += 1
    return row


def put_total(ws, row, ncols, values, merge_to=1):
    for idx, val in enumerate(values, start=1):
        c = ws.cell(row=row, column=idx, value=val)
        c.font = F_BOLD
        c.alignment = CENTER
    if merge_to > 1:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=merge_to)
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = FILL_TOTAL
        ws.cell(row=row, column=col).border = BORDER


def put_note(ws, row, ncols, text):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row=row, column=1, value=text)
    c.font = F_NOTE
    c.alignment = LEFT


def sheet_info(wb, title, info, milestones, note, widths=(16, 34, 16, 62)):
    ws = wb.create_sheet("01 项目概览")
    put_title(ws, title, 4, height=30)
    row = 2
    for key, val in info:
        c = ws.cell(row=row, column=1, value=key)
        c.font = F_BOLD
        c.alignment = LEFT
        c.border = BORDER
        c.fill = FILL_SUB
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        vc = ws.cell(row=row, column=2, value=val)
        vc.font = F_BODY
        vc.alignment = LEFT
        for col in range(2, 5):
            ws.cell(row=row, column=col).border = BORDER
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="里程碑").font = F_BOLD
    row += 1
    put_header(ws, row, ["里程碑", "名称", "日期", "验收标志"])
    row += 1
    for mid, name, mdate, flag in milestones:
        for idx, val in enumerate([mid, name, mdate, flag], start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_BODY
            c.alignment = LEFT if idx in (2, 4) else CENTER
            c.border = BORDER
        row += 1

    row += 1
    put_note(ws, row, 4, note)
    for col, width in zip("ABCD", widths):
        ws.column_dimensions[col].width = width
    return ws


def sheet_tasks(wb, title, tasks, phases, extra_headers=None, extra_getter=None):
    """extra_headers/extra_getter 用于质检表插入「需求映射」列（位于交付物之后）。"""
    headers = ["编号", "阶段", "任务名称", "任务描述", "交付物", "人日", "优先级", "开始", "结束", "状态", "负责人"]
    widths = [8, 16, 30, 50, 24, 6.5, 7, 11, 11, 9, 9]
    if extra_headers:
        headers = headers[:5] + extra_headers + headers[5:]
        widths = widths[:5] + [26] + widths[5:]
    ncols = len(headers)
    ws = wb.create_sheet("02 任务清单")
    put_title(ws, title, ncols)
    put_header(ws, 2, headers, widths)
    center = {i for i, h in enumerate(headers, start=1) if h in ("编号", "人日", "优先级", "开始", "结束", "状态", "负责人")}
    row = 3
    for t in sorted(tasks, key=lambda x: (x["start"], x["id"])):
        vals = [t["id"], f"{t['phase']} {phases[t['phase']][0]}", t["name"], t["desc"], t["deliver"]]
        if extra_getter:
            vals += extra_getter(t)
        vals += [
            t["days"],
            t["prio"],
            t["start"].strftime("%Y-%m-%d"),
            t["end"].strftime("%Y-%m-%d"),
            "未开始",
            "孟星旭",
        ]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_BODY
            c.border = BORDER
            c.alignment = CENTER if idx in center else LEFT
        row += 1
    total = sum(t["days"] for t in tasks)
    day_col = headers.index("人日") + 1
    total_vals = [""] * ncols
    total_vals[0] = "合计"
    total_vals[day_col - 1] = f"{total:.1f}"
    put_total(ws, row, ncols, total_vals, merge_to=4)
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(ncols)}{row - 1}"
    return ws


def week_label(wk, week_base=1):
    """wk 为相对起点的 0 基周索引；week_base 为起始周编号（二期从 W13 起）。"""
    return f"W{wk + week_base}"


def sheet_gantt(wb, days, start, tasks, phases, milestones, title, week_base=1):
    info_cols = ["编号", "阶段", "任务名称", "起", "止", "人日", "优先级"]
    info_widths = [8, 7, 34, 7, 7, 6, 6]
    first_day_col = len(info_cols) + 1
    last_day_col = first_day_col + len(days) - 1
    week_days = group_weeks(days, start)

    ws = wb.create_sheet("03 甘特图")
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_day_col)
    c = ws.cell(row=1, column=1, value=title)
    c.font = F_TITLE
    c.fill = FILL_TITLE
    c.alignment = CENTER
    ws.row_dimensions[1].height = 28

    for idx, (h, w) in enumerate(zip(info_cols, info_widths), start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w
        ws.merge_cells(start_row=2, start_column=idx, end_row=4, end_column=idx)
        c = ws.cell(row=2, column=idx, value=h)
        c.font = F_HEAD
        c.alignment = CENTER
        for r in (2, 3, 4):
            ws.cell(row=r, column=idx).fill = FILL_HEAD
            ws.cell(row=r, column=idx).border = BORDER

    for wk, wdays in week_days.items():
        col_from = first_day_col + days.index(wdays[0])
        col_to = first_day_col + days.index(wdays[-1])
        if col_to > col_from:
            ws.merge_cells(start_row=2, start_column=col_from, end_row=2, end_column=col_to)
        c = ws.cell(row=2, column=col_from, value=f"{week_label(wk, week_base)}\n{wdays[0]:%m-%d}~{wdays[-1]:%m-%d}")
        c.font = Font(name=FONT_NAME, size=8, bold=True, color=C_TITLE)
        c.alignment = CENTER
        for cc in range(col_from, col_to + 1):
            ws.cell(row=2, column=cc).border = BORDER
            ws.cell(row=2, column=cc).fill = FILL_SUB

    for idx, one in enumerate(days):
        col = first_day_col + idx
        ws.column_dimensions[get_column_letter(col)].width = 2.6
        c = ws.cell(row=3, column=col, value=f"{one:%m-%d}")
        c.font = Font(name=FONT_NAME, size=7)
        c.alignment = Alignment(horizontal="center", vertical="center", text_rotation=90)
        c.border = BORDER
        c2 = ws.cell(row=4, column=col, value="一二三四五六日"[one.weekday()])
        c2.font = Font(name=FONT_NAME, size=7)
        c2.alignment = CENTER
        c2.border = BORDER
    ws.row_dimensions[3].height = 34
    ws.row_dimensions[4].height = 14

    row = 5
    for t in sorted(tasks, key=lambda x: (x["start"], x["id"])):
        vals = [t["id"], t["phase"], t["name"], f"{t['start']:%m-%d}", f"{t['end']:%m-%d}", t["days"], t["prio"]]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_BOLD if idx == 1 else F_BODY
            c.alignment = CENTER if idx in (1, 2, 4, 5, 6, 7) else LEFT
            c.border = BORDER
        col_from = first_day_col + days.index(t["start"])
        col_to = first_day_col + days.index(t["end"])
        fill = PatternFill("solid", fgColor=phases[t["phase"]][1])
        c = ws.cell(row=row, column=col_from, value="")
        c.fill = fill
        c.border = BORDER
        if col_to > col_from:
            ws.merge_cells(start_row=row, start_column=col_from, end_row=row, end_column=col_to)
        for cc in range(col_from, col_to + 1):
            ws.cell(row=row, column=cc).fill = fill
            ws.cell(row=row, column=cc).border = BORDER
        ws.row_dimensions[row].height = 17
        row += 1

    row += 0
    for idx, h in enumerate(["里程碑", "评审", "验收标志", "日期", "", "", ""], start=1):
        c = ws.cell(row=row, column=idx, value=h or None)
        c.font = F_HEAD
        c.fill = FILL_HEAD
        c.alignment = CENTER
        c.border = BORDER
    row += 1
    for mid, name, mdate, flag in milestones:
        mday = parse_md(mdate[5:])
        for idx, val in enumerate([mid, "◆", name, mdate[5:], "", "", ""], start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_MS if idx == 2 else F_BODY
            c.alignment = CENTER if idx in (1, 2, 4) else LEFT
            c.border = BORDER
            c.fill = FILL_MS
        if mday in days:
            col = first_day_col + days.index(mday)
            c = ws.cell(row=row, column=col, value="◆")
            c.font = F_MS
            c.alignment = CENTER
            c.border = BORDER
            c.fill = FILL_MS
        ws.row_dimensions[row].height = 17
        row += 1

    ws.freeze_panes = ws.cell(row=5, column=first_day_col)
    return ws


def sheet_weeks(wb, days, start, tasks, focus, ms_by_week, title, total_label="合计", week_base=1):
    week_days = group_weeks(days, start)
    capacity = {w: len(v) for w, v in week_days.items()}
    workload = workload_of(tasks, start)
    headers = ["周次", "起止", "容量（人日）", "计划（人日）", "余量", "累计人日", "本周重点", "里程碑"]
    ws = wb.create_sheet("04 周度负载")
    put_title(ws, title, 8)
    put_header(ws, 2, headers, [8, 18, 12, 12, 8, 10, 52, 10])
    row = 3
    acc = 0.0
    for wk, wdays in week_days.items():
        key = week_label(wk, week_base)
        load = workload.get(wk, 0.0)
        acc += load
        ms = [mid for mid, w in ms_by_week.items() if w == key]
        vals = [key, f"{wdays[0]:%m-%d}~{wdays[-1]:%m-%d}", capacity[wk], round(load, 1),
                round(capacity[wk] - load, 1), round(acc, 1), focus.get(key, ""), "、".join(ms)]
        for idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row, column=idx, value=val)
            c.font = F_BODY
            c.border = BORDER
            c.alignment = LEFT if idx == 7 else CENTER
        row += 1
    total = sum(workload.values())
    ms_ids = list(ms_by_week)
    ms_text = ms_ids[0] if len(ms_ids) == 1 else f"{ms_ids[0]}–{ms_ids[-1]}"
    put_total(
        ws,
        row,
        8,
        [total_label, f"{days[0]:%m-%d}~{days[-1]:%m-%d}", len(days), round(total, 1),
         round(len(days) - total, 1), round(acc, 1), f"占用 {total / len(days) * 100:.1f}%，余量作返工与联调等待", ms_text],
    )
    return ws


def sheet_table(wb, sheet_name, title, headers, widths, rows, center_cols=(), note=None, total=None):
    ncols = len(headers)
    ws = wb.create_sheet(sheet_name)
    put_title(ws, title, ncols)
    put_header(ws, 2, headers, widths)
    row = put_rows(ws, 3, rows, center_cols=center_cols)
    if total:
        put_total(ws, row, ncols, total)
        row += 1
    if note:
        put_note(ws, row + 1, ncols, note)
    return ws


def sheet_legends(wb, phases, legends, version_text):
    ws = wb.create_sheet("07 图例与口径")
    put_title(ws, "图例、口径与假设", 4)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 96
    ws.merge_cells("B2:D2")
    for idx, h in enumerate(["项", "说明"], start=1):
        c = ws.cell(row=2, column=idx, value=h)
        c.font = F_HEAD
        c.alignment = CENTER
    for idx in range(1, 5):
        ws.cell(row=2, column=idx).fill = FILL_HEAD
        ws.cell(row=2, column=idx).border = BORDER

    row = 3
    for pid, (pname, color) in phases.items():
        c = ws.cell(row=row, column=1, value=f"{pid} {pname}")
        c.font = F_BODY
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = CENTER
        c.border = BORDER
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        c2 = ws.cell(row=row, column=2, value="阶段任务条颜色")
        c2.font = F_BODY
        c2.alignment = LEFT
        row += 1

    for key, desc in legends:
        c = ws.cell(row=row, column=1, value=key)
        c.font = F_BOLD
        c.fill = FILL_SUB
        c.alignment = CENTER
        c.border = BORDER
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        c2 = ws.cell(row=row, column=2, value=desc)
        c2.font = F_BODY
        c2.alignment = LEFT
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    c = ws.cell(row=row, column=1, value=version_text)
    c.font = F_NOTE
    c.alignment = LEFT
    return ws
