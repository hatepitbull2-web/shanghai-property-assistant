#!/usr/bin/env python3
"""Render Chinese customer PDFs from inventory, customer, and optional match JSON.

Dependencies: reportlab. PPTX is optional and is produced by render_reports.mjs.
No network access, private API, or paid service is required for PDF export.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape

BG = '#F5F3ED'
INK = '#172E31'
ACCENT = '#486B5B'
MUTED = '#64706D'
DEFAULT_NAMES = ['前滩·东方湾', '中海环宇玖章', '保利都汇和煦', '紫薇之星']


def clean(value, fallback='待核实'):
    if value is None or value == '' or value == '--' or str(value).startswith('页面显示'):
        return fallback
    if isinstance(value, list) and all(isinstance(item, (str, int, float)) for item in value):
        return '、'.join(map(str, value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return fallback


def field(project, key, fallback='待核实'):
    return clean(project.get('详情', {}).get(key), fallback)


def text(x, y, w, h, content, size=28, color=INK, bold=False):
    return {'kind': 'text', 'x': x, 'y': y, 'w': w, 'h': h,
            'text': str(content), 'size': size, 'color': color, 'bold': bold}


def slide(title, footer, number, subtitle=None):
    elements = [text(72, 48, 1120, 66, title, 44, INK, True),
                text(72, 661, 1110, 26, footer, 16, MUTED),
                text(1200, 661, 40, 26, f'{number:02}', 16, MUTED)]
    if subtitle:
        elements.append(text(72, 125, 1136, 58, subtitle, 23, MUTED))
    return {'background': BG, 'elements': elements, 'notes': footer}


def table(x, y, widths, row_height, values, size=23):
    return {'kind': 'table', 'x': x, 'y': y, 'w': sum(widths),
            'h': len(values) * row_height, 'widths': widths,
            'row_height': row_height, 'values': values, 'size': size}


def mortgage(price_wan, down_fraction=0.3, annual_rate=0.03, years=30):
    principal = price_wan * 10000 * (1 - down_fraction)
    r, n = annual_rate / 12, years * 12
    monthly = principal / n if r == 0 else principal * r * (1 + r) ** n / ((1 + r) ** n - 1)
    return price_wan * down_fraction, principal / 10000, round(monthly)


CHECK_LABELS = {'budget': '预算', 'area': '面积', 'district': '区域',
                'bedrooms': '卧室数', 'availability': '当前可售情况',
                'additional:commute_max_minutes': '通勤上限'}
STATUS_LABELS = {'met': '满足', 'partial': '项目范围有交集',
                 'unknown': '待核实', 'not_met': '不满足'}
FIELD_LABELS = {'price_range_wan': '总价（万元）', 'area_range_sqm': '面积（㎡）',
                'unit_price_yuan_sqm': '单价（元/㎡）', 'sale_status': '销售状态',
                'open_date': '开盘日期'}


def public_value(value):
    """Format only displayable scalar evidence, never serialize raw objects."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, (str, int, float)) for item in value):
        return ' / '.join(map(str, value))
    return '待核实'


def verification_lines(recommendation):
    """Carry every public matching condition and conflict into the client report."""
    lines, covered = [], set()
    for key, value in recommendation.get('checks', {}).items():
        if not isinstance(value, dict):
            continue
        reason = value.get('reason')
        status = STATUS_LABELS.get(value.get('status'), '待核实')
        if value.get('status') == 'partial':
            status = {'bedrooms': '仅有项目户型信息', 'availability': '仅有项目销售状态'}.get(key, status)
        label = CHECK_LABELS.get(key, '额外购房条件' if key.startswith('additional:') else key)
        if isinstance(reason, str) and reason:
            lines.append(f'{label}：{status}。{reason}')
            covered.add(reason)
    for conflict in recommendation.get('conflicts', []):
        if not isinstance(conflict, dict):
            continue
        key = conflict.get('field', '')
        label = FIELD_LABELS.get(key, key or '资料')
        message = conflict.get('message') if isinstance(conflict.get('message'), str) else '口径需核实。'
        lines.append(f'{label}口径冲突：列表为 {public_value(conflict.get("list_value"))}；详情为 {public_value(conflict.get("detail_value"))}。{message}')
        covered.add(message + ' 字段：' + key)
    for item in recommendation.get('verification_items', []):
        if isinstance(item, str) and item and item not in covered:
            lines.append('待核实：' + item)
            covered.add(item)
    return lines


def append_verification_slides(slides, name, recommendation, footer, source):
    lines = verification_lines(recommendation)
    if not lines:
        return
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    style = ParagraphStyle('verification-measure', fontName=find_font(), fontSize=23,
                           leading=30, wordWrap='CJK')
    def measure(content):
        return Paragraph(escape(content).replace('\n', '<br/>'), style).wrap(1136, 2000)[1] + 8
    current, y, part = None, 183, 0
    for index, content in enumerate(lines, 1):
        remaining = f'{index}. {content}'
        while remaining:
            if current is None:
                part += 1
                title = f'{name} 核实事项' + ('（续）' if part > 1 else '')
                current = slide(title, footer, len(slides) + 1,
                                '以下为本次匹配中的具体条件、口径冲突和后续核实事项。')
                current['notes'] = source + '\n楼盘：' + name
                slides.append(current)
                y = 183
            available = 624 - y
            height = measure(remaining)
            if height <= available:
                current['elements'].append(text(72, y, 1136, height, remaining, 23))
                y += height + 5
                remaining = ''
            elif y > 183:
                current = None
            else:
                # A single long condition can continue onto a new slide without
                # dropping its text or reducing the normal reading size.
                lo, hi = 1, len(remaining)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if measure(remaining[:mid]) <= available:
                        lo = mid
                    else:
                        hi = mid - 1
                chunk, remaining = remaining[:lo], remaining[lo:]
                current['elements'].append(text(72, y, 1136, measure(chunk), chunk, 23))
                current = None


def build_model(inventory, customer, matches=None, mortgage_result=None):
    projects = inventory.get('楼盘', inventory.get('projects', []))
    lookup = {p.get('名称', p.get('name')): p for p in projects}
    recommendations = (matches or {}).get('recommendations', [])
    names = [r['name'] for r in recommendations if r.get('name') in lookup]
    if not matches:
        names = [n for n in DEFAULT_NAMES if n in lookup]
    if not names:
        raise ValueError('没有可用候选。请先生成匹配结果，或提供含详情的演示楼盘数据。')
    names = names[:4]
    match_by_name = {r.get('name'): r for r in recommendations}
    selected = []
    aliases = {'address': '地址', 'unit_price_yuan_sqm': '参考单价_元每平方米',
               'developer': '开发商', 'property_manager': '物业公司',
               'property_fee': '物业费', 'handover_date': '交房日期',
               'sale_status': '销售状态'}
    for name in names:
        p = copy.deepcopy(lookup[name])
        facts = match_by_name.get(name, {}).get('facts', {})
        d = p.setdefault('详情', {})
        for english, chinese in aliases.items():
            if facts.get(english) is not None:
                d[chinese] = facts[english]
        for english, chinese in [('price_range_wan', '参考总价_万元'), ('area_range_sqm', '参考面积段_平方米')]:
            value = facts.get(english)
            raw = facts.get('field_sources', {}).get(english, {}).get('detail')
            if value is not None:
                d[chinese] = raw or '-'.join(f'{n:g}' for n in value)
        for key, value in facts.get('features', {}).items():
            if value is not None:
                d[key] = value
        p['区域'] = match_by_name.get(name, {}).get('district', p.get('区域', '待核实'))
        selected.append(p)
    hard = customer.get('hard_constraints', {})
    prefs = customer.get('preferences', {})
    simulation = bool(customer.get('is_simulation', False))
    label = clean(customer.get('label'), '客户选房方案')
    marker = '模拟客户演示' if simulation else '客户初筛方案'
    collection_date = inventory.get('采集日期', '待核实')
    report_date = customer.get('as_of', '待核实')
    origin = clean(inventory.get('来源', inventory.get('source')), '来源待核实')
    source = (f'完全虚构的开源演示数据，日期 {collection_date}。不代表任何真实项目。'
              if inventory.get('is_synthetic') else
              f'{origin}，采集 {collection_date}。展示值待开发商和实际房源核实。')
    footer = f'{marker}   数据采集 {collection_date}   方案日期 {report_date}'
    budget = hard.get('budget_max_wan')
    budget_label = f'{budget:,} 万元以内' if budget is not None else '待确认'
    min_area, max_area = hard.get('area_min_sqm'), hard.get('area_max_sqm')
    area_label = f'{clean(min_area, "待确认")}-{clean(max_area, "待确认")} ㎡'
    districts = '、'.join(prefs.get('districts', [])) or '待确认'
    bedrooms_label = f'至少 {hard["bedrooms_min"]} 房' if hard.get('bedrooms_min') is not None else '待确认'
    all_slides = []
    cover = {'background': INK, 'notes': source, 'elements': [
        text(80, 88, 1120, 36, marker, 24, '#CAD8CB'),
        text(80, 202, 1100, 200, '上海新房\n客户选房方案', 68, '#FFFFFF', True),
        text(84, 427, 1080, 60, f'{label}  /  {len(selected)} 个候选楼盘', 29, '#D7DED7'),
        text(84, 575, 1075, 66, f'资料采集 {collection_date}    方案日期 {report_date}\n楼盘范围初筛，实际可售房源及购房资格待核实。', 22, '#C4CEC6')
    ]}
    all_slides.append(cover)
    s = slide('购房需求与筛选边界', footer, 2)
    s['elements'] += [
        text(72, 178, 520, 35, '已知条件', 28, ACCENT, True),
        text(72, 234, 520, 258,
             f'房款预算：{budget_label}\n建筑面积：{area_label}\n意向区域：{districts}\n房间需求：{bedrooms_label}\n偏好：' + ('、'.join(prefs.get('features', [])) or '待确认'), 29),
        text(690, 178, 480, 35, '首次带看前补充', 28, ACCENT, True),
        text(690, 234, 480, 274,
             '首付款与可承受月供\n家庭成员及购房资格\n工作地点和通勤上限\n最晚入住时间\n楼层、朝向与不可接受项', 29),
        text(72, 552, 1125, 70, '当前候选均待核实。价格与面积需绑定同一套房源，尚未确认任何楼盘满足全部条件。实际房号、房间数及可售状态需逐套确认。', 24, MUTED)]
    all_slides.append(s)
    s = slide('候选楼盘横向对比', footer, 3, f'取匹配结果前 {len(selected)} 个候选，均待核实。参考总价为项目范围，未绑定具体可售户型。')
    area = lambda p: field(p, '参考面积段_平方米', field(p, '参考面积段', clean(p.get('展示面积_平方米')))).replace('平方米', '').replace('㎡', '')
    def price(p):
        v = p.get('详情', {}).get('参考单价_元每平方米', p.get('展示单价_元每平方米'))
        return f'{v:,.0f}' if isinstance(v, (float, int)) else clean(v)
    values = [
        ['比较项目'] + names,
        ['区域 / 板块'] + [f'{p.get("区域", "待核实")}\n{p.get("板块", "待核实")}' for p in selected],
        ['单价 元/㎡'] + [price(p) for p in selected],
        ['总价 / 万元'] + [field(p, '参考总价_万元') for p in selected],
        ['面积 / ㎡'] + [area(p) for p in selected],
        ['展示交房日'] + [field(p, '交房日期') for p in selected],
        ['连廊 / 车位比'] + [f'{field(p, "连廊")}\n{field(p, "车位比")}' for p in selected],
    ]
    widths = [168] + [968 / len(selected)] * len(selected)
    s['elements'] += [table(72, 205, widths, 55, values, 21),
                      text(72, 605, 1125, 40, '不同页面面积口径可能不同，详见后页。认购信息及网签展示不能证明当前有房可买。', 20, MUTED)]
    all_slides.append(s)
    for p in selected:
        name = p['名称']
        headline = f'{p.get("区域", "区域待核")} / {p.get("板块", "板块待核")}，具体房号与总价待核实'
        fit = f'参考单价 {price(p)} 元/㎡。\n详情面积 {area(p)} ㎡。\n连廊：{field(p, "连廊")}，人车分流：{field(p, "人车分流")}。\n阳台：{field(p, "阳台封闭")}。'
        total = field(p, '参考总价_万元')
        nums = re.findall(r'\d+(?:\.\d+)?', total)
        recommendation = match_by_name.get(name, {})
        budget_check = recommendation.get('checks', {}).get('budget')
        if isinstance(budget_check, dict):
            price_note = '预算检查：' + STATUS_LABELS.get(budget_check.get('status'), '待核实') + '。'
        elif budget is None:
            price_note = '客户预算待确认，暂不能判断预算匹配。'
        elif len(nums) >= 2 and float(nums[1]) > budget:
            price_note = f'总价上限 {nums[1]} 万元，部分超出预算。'
        elif len(nums) >= 2:
            price_note = '展示总价范围在预算内，实际房号待核。'
        else:
            price_note = '参考总价待补，尚不能认定符合预算。'
        handover = field(p, '交房日期')
        if recommendation.get('conflicts'):
            second_note = f'存在 {len(recommendation["conflicts"])} 项资料口径冲突，详见核实页。'
        elif handover != '待核实' and handover < report_date:
            second_note = '展示交房日已过，需确认实际交付状态。'
        elif field(p, '连廊') == '有' and '无连廊' in prefs.get('features', []):
            second_note = '有连廊，与无连廊偏好有冲突。'
        elif clean(p.get('展示面积_平方米')) != area(p):
            second_note = f'列表面积 {clean(p.get("展示面积_平方米"))} ㎡，口径不同。'
        else:
            second_note = '户型、采光与噪声需结合房号现场确认。'
        tradeoff = price_note + '\n' + second_note + '\n' + ('具体核实条件见本盘后页。' if recommendation else '房源状态和合同交付附件需再次核对。')
        s = slide(name, footer, len(all_slides) + 1, headline)
        # Speaker notes are part of the client deliverable. Only source metadata
        # and the already visible project name are allowed here.
        s['notes'] = source + '\n项目：' + name
        s['elements'] += [
            text(72, 202, 1100, 48, field(p, '地址'), 25, MUTED),
            text(72, 283, 540, 36, '值得核实的条件', 29, ACCENT, True),
            text(72, 331, 540, 185, fit, 27),
            text(688, 283, 520, 36, '取舍与待确认项', 29, ACCENT, True),
            text(688, 331, 520, 185, tradeoff, 26),
            text(72, 534, 1135, 100,
                 f'物业：{field(p, "物业公司")}    物业费：{field(p, "物业费")}\n交房日期：{field(p, "交房日期")}    装修：{field(p, "装修")}', 22, MUTED)]
        all_slides.append(s)
        append_verification_slides(all_slides, name, recommendation, footer, source)
    mortgage_input = (mortgage_result or {}).get('inputs', {})
    if mortgage_input.get('method', 'equal-payment') != 'equal-payment':
        raise ValueError('此版月供比较页仅支持 equal-payment（等额本息），请勿混用其他还款结果。')
    illustration_price = mortgage_input.get('price_wan', min(1000, budget) if budget else 1000)
    down_fraction = mortgage_input.get('down_payment_wan', illustration_price * .3) / illustration_price
    annual_rate = mortgage_input.get('annual_rate_percent', 3.0)
    years = mortgage_input.get('years', 30)
    s = slide('首付与月供的假设测算', footer, len(all_slides) + 1,
              f'纯计算示例：首付 {down_fraction:.0%}，固定年利率 {annual_rate:.2f}%，{years:g} 年等额本息。不是政策或银行报价。')
    price_points = [illustration_price, min(1400, budget), budget] if budget else [illustration_price]
    rows = [['假设房款 / 万元', '首付款 / 万元', '贷款额 / 万元', '月供 / 元']]
    for amount in sorted(set(price_points)):
        down, loan, payment = mortgage(amount, down_fraction, annual_rate / 100, int(years))
        rows.append([f'{amount:,.0f}', f'{down:,.0f}', f'{loan:,.0f}', f'{payment:,}'])
    s['elements'] += [table(72, 225, [284] * 4, 68, rows, 28),
                      text(72, 544, 1125, 64, '房款外还需预留税费、维修资金、车位及其他费用。此处未计入公积金贷款、税费和一次性费用。实际额度、利率及年限以银行审批为准。', 24, MUTED)]
    all_slides.append(s)
    s = slide('带看安排与房源核实清单', footer, len(all_slides) + 1)
    s['elements'] += [
        text(72, 175, 520, 42, '预约前', 30, ACCENT, True),
        text(72, 234, 520, 276, '每盘获取 2-3 套具体房号\n确认户型、楼层、朝向、总价\n核对可售状态及优惠条件\n确认认购和交付的最新口径\n取得户型图及合同交付附件', 28),
        text(695, 175, 510, 42, '现场及带看后', 30, ACCENT, True),
        text(695, 234, 510, 276, '现场检查采光、噪声和动线\n记录喜欢与不满意的具体原因\n核实每项疑问并保存依据\n按真实反馈调整候选顺序\n下一次带看前补齐材料', 28),
        text(72, 558, 1136, 59, source + ' 未采集或未核实项保留为待核实。', 22, MUTED)]
    all_slides.append(s)
    for sl in all_slides:
        sl['notes'] = sl.get('notes', '') + '\n资料采集日期：' + collection_date
    return {'width': 1280, 'height': 720, 'font': 'Heiti SC', 'simulation': simulation,
            'source_label': source,
            'selected_names': names, 'source_date': collection_date,
            'report_date': report_date, 'slides': all_slides}


def find_font(explicit=None):
    paths = [explicit, os.environ.get('REPORT_CJK_FONT'),
             '/System/Library/Fonts/STHeiti Light.ttc',
             '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for p in paths:
        if not p or not Path(p).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont('ReportCN', p, subfontIndex=0))
            return 'ReportCN'
        except Exception:
            continue
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'


def render_pdf(model, output, font_path=None):
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    font = find_font(font_path)
    width, height = model['width'], model['height']
    c = canvas.Canvas(str(output), pagesize=(width * .75, height * .75))
    c.setTitle('上海新房客户选房方案' if len(model['slides']) > 3 else '带看复盘演示')
    c.setAuthor('上海房产销售助理')
    for page, s in enumerate(model['slides'], 1):
        c.saveState()
        c.scale(.75, .75)
        c.setFillColor(HexColor(s['background']))
        c.rect(0, 0, width, height, fill=1, stroke=0)
        def paragraph(content, x, y, w, h, size, color, leading=1.30):
            style = ParagraphStyle('CN', fontName=font, fontSize=size,
                                   leading=size * leading, textColor=HexColor(color),
                                   wordWrap='CJK', spaceAfter=0, spaceBefore=0)
            para = Paragraph(escape(str(content)).replace('\n', '<br/>'), style)
            pw, ph = para.wrap(w, h)
            if ph > h + 2:
                raise ValueError(f'第 {page} 页文本溢出 {ph:.1f}>{h}: {str(content)[:60]}')
            para.drawOn(c, x, height - y - ph)
        for e in s['elements']:
            if e['kind'] == 'text':
                paragraph(e['text'], e['x'], e['y'], e['w'], e['h'], e['size'], e['color'])
                continue
            for ri, row in enumerate(e['values']):
                xx = e['x']
                yy = e['y'] + ri * e['row_height']
                for ci, value in enumerate(row):
                    ww = e['widths'][ci]
                    fill = INK if ri == 0 else ('#E5EBE4' if ci == 0 else ('#FFFFFF' if ri % 2 else '#ECEFE9'))
                    c.setFillColor(HexColor(fill))
                    c.rect(xx, height - yy - e['row_height'], ww, e['row_height'], fill=1, stroke=0)
                    paragraph(value, xx + 12, yy + 6, ww - 24, e['row_height'] - 10,
                              e['size'], '#FFFFFF' if ri == 0 else INK, 1.02)
                    xx += ww
        c.restoreState()
        c.showPage()
    c.save()


def recap_model(model, viewing=None):
    names = ['模拟海棠苑（虚构项目）', '模拟云杉府（虚构项目）']
    if viewing:
        names = [p['project'] for p in viewing.get('projects', [])][:2]
    if len(names) < 2:
        raise ValueError('带看演示需两个虚构项目反馈。')
    foot = '虚拟带看反馈演示   非真实客户陈述   ' + model['report_date']
    s = slide('带看后的选择与下一步', foot, 1, '独立虚构案例，与选房方案的真实项目无关。以下反馈仅用于演示复盘效果。')
    feedback = viewing.get('projects', []) if viewing else []
    if feedback:
        descriptions = []
        for p in feedback[:2]:
            descriptions.append('喜欢：' + '；'.join(p.get('likes', [])) + '\n待改善：' + '；'.join(p.get('dislikes', [])) + '\n意向：' + p.get('intention', '待确认'))
    else:
        descriptions = ['喜欢客餐厅空间，想核实通勤。\n下一步：实测早高峰并取得书面报价。', '认为次卧偏小，希望比较其他户型。\n下一步：测量次卧净尺寸与家具布置。']
    s['elements'] += [
        text(72, 207, 530, 38, names[0], 30, ACCENT, True),
        text(72, 265, 530, 232, descriptions[0], 27),
        text(688, 207, 520, 38, names[min(1, len(names)-1)], 30, ACCENT, True),
        text(688, 265, 520, 232, descriptions[1], 27),
        text(72, 536, 1136, 94, '后续核实：实测工作日早高峰通勤，取得书面费用明细，测量次卧家具摆放后的净通行空间。\n新增偏好：更加重视次卧实际可用空间。通勤上限和区域调整意愿仍待客户确认。', 25, MUTED)]
    return {'width': 1280, 'height': 720, 'font': model['font'], 'slides': [s]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inventory', required=True)
    ap.add_argument('--customer', required=True)
    ap.add_argument('--match-json')
    ap.add_argument('--mortgage-json')
    ap.add_argument('--viewing-json')
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--work-dir', default='work/report-build')
    ap.add_argument('--font')
    ap.add_argument('--demo-recap', action='store_true', help='另输出明确标注虚构反馈的带看演示')
    args = ap.parse_args()
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    inventory, customer = read(args.inventory), read(args.customer)
    matches = read(args.match_json) if args.match_json else None
    if matches and matches.get('customer'):
        match_customer = matches['customer']
        for key in ['hard_constraints', 'preferences', 'as_of', 'is_simulation']:
            if customer.get(key) != match_customer.get(key):
                raise ValueError(f'客户输入与匹配结果的 {key} 不一致，请重新匹配后导出。')
        customer = match_customer
    if matches is None and not customer.get('is_simulation'):
        raise ValueError('正式客户必须提供 --match-json，禁止使用固定演示候选。')
    mortgage_result = read(args.mortgage_json) if args.mortgage_json else None
    viewing = read(args.viewing_json) if args.viewing_json else None
    if args.demo_recap and (not viewing or viewing.get('is_simulation') is not True):
        raise ValueError('--demo-recap 只接受显式 is_simulation:true 的 --viewing-json 输入。正式复盘请使用 render_viewing.py。')
    model = build_model(inventory, customer, matches, mortgage_result)
    out, work = Path(args.output_dir), Path(args.work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    stem = '上海新房_客户选房方案_演示' if model['simulation'] else '上海新房_客户选房方案'
    model_path = work / 'report-model.json'
    inputs = {Path(p).resolve() for p in [args.inventory, args.customer, args.match_json, args.mortgage_json, args.viewing_json] if p}
    targets = [model_path, out / (stem + '.pdf')]
    if args.demo_recap:
        targets.append(out / '带看复盘_演示.pdf')
    if inputs.intersection({p.resolve() for p in targets}):
        raise ValueError('输出路径与输入文件冲突，拒绝覆盖输入。')
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding='utf-8')
    render_pdf(model, out / (stem + '.pdf'), args.font)
    if args.demo_recap:
        render_pdf(recap_model(model, viewing), out / '带看复盘_演示.pdf', args.font)
    print(json.dumps({'pdf': str(out / (stem + '.pdf')), 'model': str(model_path),
                      'slides': len(model['slides']), 'selected': model['selected_names']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
