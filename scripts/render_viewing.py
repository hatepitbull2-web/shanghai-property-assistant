#!/usr/bin/env python3
"""Render an actual or simulated viewing_report JSON to a paginated Chinese PDF.

python3 scripts/render_viewing.py --input viewing-report.json --output report.pdf
Requires reportlab>=4.0. No inventory, account, or network access is required.
"""
import argparse
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape


def validate(report):
    if not isinstance(report, dict) or report.get('type') != 'viewing_report':
        raise ValueError('输入必须为 assistant.py viewing 生成的 viewing_report JSON。')
    if not isinstance(report.get('is_simulation'), bool):
        raise ValueError('is_simulation 必须明确为 true 或 false，禁止推断是否为模拟反馈。')
    projects = report.get('projects')
    if not isinstance(projects, list) or not projects:
        raise ValueError('projects 必须至少包含一个带看项目。')
    for index, project in enumerate(projects, 1):
        if not isinstance(project, dict) or not isinstance(project.get('project'), str) or not project['project'].strip():
            raise ValueError(f'第 {index} 个带看项目缺少 project 名称。')
        for field in ('likes', 'dislikes', 'questions', 'to_verify', 'next_steps'):
            value = project.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f'第 {index} 个项目的 {field} 必须是文本数组。')
        for field in ('unit', 'customer_quote', 'intention', 'note'):
            if project.get(field) is not None and not isinstance(project[field], str):
                raise ValueError(f'第 {index} 个项目的 {field} 必须是文本或 null。')
    for field in ('next_steps',):
        value = report.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f'{field} 必须是文本数组。')
    updates = report.get('preference_updates', {})
    if not isinstance(updates, dict):
        raise ValueError('preference_updates 必须是对象。')
    for key in ('confirmed', 'to_confirm'):
        values = updates.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f'preference_updates.{key} 必须是文本数组。')
    return report


def font_family(explicit=None):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = [explicit, os.environ.get('REPORT_CJK_FONT'),
                  '/System/Library/Fonts/STHeiti Light.ttc',
                  '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont('ViewingCN', candidate, subfontIndex=0))
            return 'ViewingCN'
        except Exception:
            continue
    if explicit:
        raise ValueError('指定字体不可用。请提供可嵌入的中文 TrueType .ttf 或 .ttc 文件。')
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'


def pdf_text(value):
    return escape(str(value)).replace('\n', '<br/>')


def render_viewing(report, output, font=None):
    report = validate(report)
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    family = font_family(font)
    ink, green, muted = HexColor('#172E31'), HexColor('#486B5B'), HexColor('#64706D')
    styles = {
        'title': ParagraphStyle('title', fontName=family, fontSize=25, leading=33, textColor=ink, spaceAfter=9),
        'subtitle': ParagraphStyle('subtitle', fontName=family, fontSize=10.5, leading=16, textColor=muted, spaceAfter=5),
        'project': ParagraphStyle('project', fontName=family, fontSize=19, leading=27, textColor=ink, spaceBefore=13, spaceAfter=9, keepWithNext=True),
        'heading': ParagraphStyle('heading', fontName=family, fontSize=12.5, leading=19, textColor=green, spaceBefore=11, spaceAfter=4, keepWithNext=True),
        'body': ParagraphStyle('body', fontName=family, fontSize=11.5, leading=17, textColor=ink, spaceAfter=4, wordWrap='CJK', splitLongWords=True),
        'note': ParagraphStyle('note', fontName=family, fontSize=9.5, leading=14, textColor=muted, spaceBefore=5, spaceAfter=6, wordWrap='CJK'),
    }
    simulated = report['is_simulation']
    title = '带看复盘报告（演示）' if simulated else '带看复盘报告'
    marker = '模拟反馈，非真实客户陈述' if simulated else '客户现场反馈记录'
    date = report.get('viewing_date') or '日期待确认'
    customer = report.get('customer_label') or '客户未命名'
    story = [Paragraph(pdf_text(title), styles['title']),
             Paragraph(pdf_text(f'{customer}    带看日期：{date}'), styles['subtitle']),
             Paragraph(pdf_text(f'{marker}。本报告包含 {len(report["projects"])} 个项目。'), styles['subtitle']),
             Spacer(1, 7)]

    def add_section(label, items):
        story.append(Paragraph(pdf_text(label), styles['heading']))
        if not items:
            story.append(Paragraph('未记录', styles['body']))
            return
        for number, item in enumerate(items, 1):
            story.append(Paragraph(pdf_text(f'{number}. {item}'), styles['body']))

    project_steps = set()
    for index, project in enumerate(report['projects'], 1):
        if index > 1:
            story.append(PageBreak())
        story.append(Paragraph(pdf_text(f'{index:02}  {project["project"]}'), styles['project']))
        metadata = []
        metadata.append('房源 / 户型：' + (project.get('unit') or '未记录'))
        metadata.append('客户意向：' + (project.get('intention') or '未记录'))
        story.append(Paragraph(pdf_text('\n'.join(metadata)), styles['body']))
        if project.get('customer_quote'):
            story.append(Paragraph('客户原话', styles['heading']))
            story.append(Paragraph(pdf_text(project['customer_quote']), styles['body']))
        for field, label in [('likes', '喜欢的地方'), ('dislikes', '不满意或担心的地方'),
                             ('questions', '客户提出的问题'), ('to_verify', '需要核实的事项'),
                             ('next_steps', '这个项目的下一步')]:
            add_section(label, project.get(field, []))
        project_steps.update(project.get('next_steps', []))
        if project.get('note'):
            story.append(Paragraph(pdf_text(project['note']), styles['note']))

    # Identical project actions are already printed verbatim. Only additional
    # overall actions need another entry; this prevents repeating a single visit.
    additional_steps = [item for item in report.get('next_steps', []) if item not in project_steps]
    # Only these public fields may appear in a customer-facing report. Internal
    # commission, negotiation or other arbitrary keys are never rendered.
    raw_updates = report.get('preference_updates', {})
    updates = {key: raw_updates.get(key, []) for key in ('confirmed', 'to_confirm')}
    has_updates = any(updates.values())
    if has_updates or additional_steps:
        story.append(PageBreak())
        story.append(Paragraph('偏好更新与后续安排', styles['title']))
        for key, label in [('confirmed', '已确认的偏好变化'), ('to_confirm', '需要客户进一步确认')]:
            if updates.get(key):
                add_section(label, updates[key])
        if additional_steps:
            add_section('补充后续事项', additional_steps)
    if report.get('scope_note'):
        story.append(Spacer(1, 10))
        story.append(Paragraph(pdf_text(report['scope_note']), styles['note']))
    if simulated:
        story.append(Paragraph('此报告使用模拟反馈，仅用于展示服务流程和报告格式。', styles['note']))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=44, leftMargin=44,
                                 topMargin=42, bottomMargin=49, title=title, author='上海房产销售助理')
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(family, 8.5)
        canvas.setFillColor(muted)
        canvas.drawString(44, 26, marker)
        canvas.drawRightString(A4[0] - 44, 26, str(doc.page))
        canvas.restoreState()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', '--viewing-json', dest='input', required=True,
                        help='assistant.py viewing 输出的 viewing_report JSON')
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument('--output', help='目标 PDF 文件路径')
    destination.add_argument('--output-dir', help='输出目录，自动按是否演示命名 PDF')
    parser.add_argument('--font', help='可嵌入的中文 TrueType 字体路径')
    args = parser.parse_args(argv)
    source = Path(args.input)
    report = validate(json.loads(source.read_text(encoding='utf-8')))
    filename = '带看复盘报告_演示.pdf' if report['is_simulation'] else '带看复盘报告.pdf'
    target = Path(args.output) if args.output else Path(args.output_dir) / filename
    if target.resolve() == source.resolve():
        raise ValueError('输出路径与输入文件冲突，拒绝覆盖反馈原文。')
    if target.suffix.lower() != '.pdf':
        raise ValueError('输出文件须使用 .pdf 扩展名。')
    render_viewing(report, target, args.font)
    print(json.dumps({'pdf': str(target), 'projects': len(report['projects']),
                      'is_simulation': report['is_simulation']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
