"""把 SGF 主分支转换成数字手记棋谱，直接输出适合打印的 PDF。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
import os
import sys
from pathlib import Path
import re

PROJECT_DIR = Path(__file__).resolve().parent

@dataclass
class Tree:
    nodes: list[dict[str, list[str]]] = field(default_factory=list)
    children: list[Tree] = field(default_factory=list)


class SGFParser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def whitespace(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text[self.pos:self.pos + 2] in ('\\r', '\\n'):
                # 部分平台导出时把结构间换行保存成字面量反斜杠序列。
                self.pos += 2
            else:
                break

    def value(self):
        self.pos += 1  # [
        result = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == ']':
                return ''.join(result)
            if char == '\\':
                if self.pos >= len(self.text):
                    break
                char = self.text[self.pos]
                self.pos += 1
                if char in '\r\n':
                    if char == '\r' and self.text[self.pos:self.pos + 1] == '\n':
                        self.pos += 1
                    continue
            result.append(char)
        raise ValueError('SGF 属性缺少右方括号 ]')

    def tree(self):
        self.whitespace()
        if self.text[self.pos:self.pos + 1] != '(':
            raise ValueError('SGF 棋谱必须以 ( 开始')
        self.pos += 1
        tree = Tree()
        self.whitespace()
        while self.text[self.pos:self.pos + 1] == ';':
            self.pos += 1
            node = {}
            self.whitespace()
            while self.pos < len(self.text) and self.text[self.pos].isupper():
                start = self.pos
                while self.pos < len(self.text) and self.text[self.pos].isupper():
                    self.pos += 1
                key = self.text[start:self.pos]
                self.whitespace()
                values = []
                while self.text[self.pos:self.pos + 1] == '[':
                    values.append(self.value())
                    self.whitespace()
                if not values:
                    raise ValueError(f'属性 {key} 缺少值')
                node.setdefault(key, []).extend(values)
            tree.nodes.append(node)
        while self.text[self.pos:self.pos + 1] == '(':
            tree.children.append(self.tree())
            self.whitespace()
        if self.text[self.pos:self.pos + 1] != ')' or not tree.nodes:
            raise ValueError('SGF 棋树结构不完整')
        self.pos += 1
        return tree


def read_sgf(path: Path):
    data = path.read_bytes()
    match = re.search(rb'CA\[([^]]+)]', data)
    encodings = [match.group(1).decode('ascii')] if match else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            return SGFParser(data.decode(encoding)).tree()
        except (UnicodeError, LookupError):
            continue
    raise ValueError('无法解码 SGF，请检查 CA 字符编码属性')


def mainline(tree):
    yield from tree.nodes
    if tree.children:
        yield from mainline(tree.children[0])


def point(value, size):
    if len(value) != 2:
        raise ValueError(f'无效坐标：{value!r}')
    def axis(char):
        return ord(char) - ord('a') if 'a' <= char <= 'z' else ord(char) - ord('A') + 26
    x, y = map(axis, value)
    if not (0 <= x < size and 0 <= y < size):
        raise ValueError(f'坐标 {value!r} 超出 {size} 路棋盘')
    return x, y


def collect(tree):
    nodes = list(mainline(tree))
    root = nodes[0]
    board_size = root.get('SZ', ['19'])[0]
    if board_size != '19':
        raise ValueError(f'仅支持 19 路棋盘，当前棋谱的 SZ 为 {board_size!r}')
    size = 19
    spots = defaultdict(list)
    setup = {}
    passes = []
    number = 0
    for node in nodes:
        for key in ('AB', 'AW', 'AE'):
            for value in node.get(key, []):
                corners = value.split(':')
                a = point(corners[0], size)
                b = point(corners[-1], size)
                for x in range(min(a[0], b[0]), max(a[0], b[0]) + 1):
                    for y in range(min(a[1], b[1]), max(a[1], b[1]) + 1):
                        if key == 'AE':
                            setup.pop((x, y), None)
                        else:
                            setup[(x, y)] = key[-1]
        for color in ('B', 'W'):
            if color not in node:
                continue
            number += 1
            value = node[color][0]
            if not value or (value == 'tt' and size <= 19):
                passes.append((number, color))
            else:
                spots[point(value, size)].append((number, color))
    return root, size, spots, setup, passes, number


def pdf_dependencies():
    try:
        import reportlab
    except ImportError:
        raise ValueError('缺少 reportlab，请运行 python -m pip install reportlab') from None


def chinese_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    # noinspection SpellCheckingInspection
    candidates = [os.environ.get('SGF_FONT', ''), 'C:/Windows/Fonts/msyh.ttc',
                  'C:/Windows/Fonts/simsun.ttc', '/usr/share/fonts/truetype/arphic/uming.ttc']
    for path in candidates:
        if path and Path(path).is_file():
            pdfmetrics.registerFont(TTFont('RecordChinese', path))
            return 'RecordChinese'
    raise ValueError('未找到中文字体；请设置 SGF_FONT 为中文 TrueType 字体文件路径')


def metadata(root, count):
    result = []
    for color, name, rank, team in [('黑方', 'PB', 'BR', 'BT'), ('白方', 'PW', 'WR', 'WT')]:
        parts = [root[k][0] for k in (name, rank, team) if root.get(k, [''])[0]]
        if parts:
            result.append(color + '：' + ' / '.join(parts))
    labels = [('EV', '赛事'), ('RO', '轮次'), ('DT', '日期'), ('PC', '地点'),
              ('RE', '结果'), ('RU', '规则'), ('KM', '贴目（SGF 原值）'),
              ('HA', '让子'), ('TM', '基本用时（秒）'), ('OT', '读秒'),
              ('TC', '读秒次数（平台字段）'), ('TT', '读秒时长（平台字段）'),
              ('US', '录谱者'), ('SO', '来源'), ('AN', '注解者'),
              ('CP', '版权'), ('ON', '布局'), ('GC', '对局说明')]
    for key, label in labels:
        if root.get(key, [''])[0]:
            value = root[key][0]
            if key == 'RE':
                value = {'W+R': '白中盘胜', 'B+R': '黑中盘胜',
                         'W+T': '白超时胜', 'B+T': '黑超时胜', '0': '和棋'}.get(value, value)
            if key == 'RU':
                value = {'Chinese': '中国规则', 'Japanese': '日本规则',
                         'Korean': '韩国规则'}.get(value, value)
            result.append(label + '：' + value)
    result.append(f'总手数：{count}')
    return result


def write_pdf(tree, destination):
    pdf_dependencies()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Flowable, Table, TableStyle
    root, size, spots, setup, passes, count = collect(tree)
    font = chinese_font()
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    # noinspection SpellCheckingInspection
    korean_path = Path('C:/Windows/Fonts/malgun.ttf')
    if korean_path.is_file():
        pdfmetrics.registerFont(TTFont('RecordKorean', str(korean_path)))
    def korean_span(match: re.Match[str]) -> str:
        return '<font name="RecordKorean">' + match.group(0) + '</font>'

    def rich_text(text: str) -> str:
        escaped_text: str = str(escape(text)).replace('\n', '<br/>').replace('\\n', '<br/>')
        if korean_path.is_file():
            escaped_text = re.sub(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]+',
                                  korean_span, escaped_text)
        return escaped_text
    red = '#cf202c'
    style = ParagraphStyle('info', fontName=font, fontSize=9, leading=13,
                           wordWrap='CJK', spaceAfter=2)
    repeat_style = ParagraphStyle('repeated_moves', fontName='Helvetica-Bold',
                                  fontSize=10, leading=14)
    title_style = ParagraphStyle('title', parent=style, fontSize=18, leading=24,
                                 alignment=1, spaceAfter=10)
    def paragraph(text):
        return Paragraph(rich_text(text), style)
    def colored(number, color):
        return f'<font color="{red if color == "W" else "#171717"}">{number}</font>'

    class RepeatBoxes(Flowable):
        """每处重复落点一个框，依次排列，超宽时在框内换行。"""
        def __init__(self, groups):
            super().__init__()
            self.width = 148 * mm
            self.hAlign = 'CENTER'
            self.items = []
            x = y = row_height = 0
            padding, gap = 6, 7
            for moves in groups:
                plain = str(moves[0][0]) + ' = ' + ', '.join(str(n) for n, _ in moves[1:])
                width = min(self.width, pdfmetrics.stringWidth(plain, 'Helvetica-Bold', 10) + padding*2)
                text = colored(*moves[0]) + ' = ' + ', '.join(colored(n, c) for n, c in moves[1:])
                para = Paragraph(text, repeat_style)
                _, text_height = para.wrap(width-padding*2, 10000)
                height = text_height + padding*2
                if x and x + width > self.width:
                    y += row_height + gap
                    x = row_height = 0
                self.items.append((x, y, width, height, para))
                x += width + gap
                row_height = max(row_height, height)
            self.height = y + row_height

        def draw(self):
            c = self.canv
            for x, y, width, height, para in self.items:
                bottom = self.height-y-height
                c.setFillColor(colors.HexColor('#f4f4f4'))
                c.setStrokeColor(colors.HexColor('#bcbcbc'))
                c.setLineWidth(.5)
                c.roundRect(x, bottom, width, height, 3, fill=1, stroke=1)
                para.drawOn(c, x+6, bottom+6)

    class Board(Flowable):
        def __init__(self):
            super().__init__()
            self.width = 166 * mm
            self.height = 166 * mm
            self.hAlign = 'CENTER'

        def draw(self):
            c = self.canv
            inset = 9 * mm
            length = self.width - inset * 2
            step = length / (size - 1)
            def xy(column_index, row_index):
                return inset + column_index * step, self.height - inset - row_index * step
            c.setStrokeColor(colors.HexColor('#555555'))
            c.setLineWidth(0.45)
            for i in range(size):
                x, y = xy(i, i)
                c.line(inset, y, inset + length, y)
                c.line(x, inset, x, inset + length)
            stars = [3, 9, 15]
            star_points = [(x, y) for x in stars for y in stars]
            c.setFillColor(colors.black)
            for x, y in star_points:
                c.circle(*xy(x, y), 1.5, stroke=0, fill=1)
            def chinese_number(n):
                digits = '零一二三四五六七八九'
                if n < 10:
                    return digits[n]
                tens, ones = divmod(n, 10)
                return (digits[tens] if tens > 1 else '') + '十' + (digits[ones] if ones else '')
            c.setFont(font, 7)
            for i in range(size):
                x, y = xy(i, i)
                c.drawCentredString(x, self.height - 3 * mm, str(i + 1))
                c.drawCentredString(x, 2 * mm, str(i + 1))
                c.drawCentredString(2.5 * mm, y - 2.3, chinese_number(i + 1))
                c.drawCentredString(self.width - 2.5 * mm, y - 2.3, chinese_number(i + 1))
            for (x, y), color in setup.items():
                if (x, y) not in spots:
                    c.setFillColor(colors.black if color == 'B' else colors.white)
                    c.setStrokeColor(colors.black)
                    c.circle(*xy(x, y), step * .34, stroke=1, fill=1)
            for (x, y), moves in spots.items():
                number, color = moves[0]
                cx, cy = xy(x, y)
                text = str(number)
                font_size = min(11, step * .68, step * 1.45 / len(text))
                c.setFont('Helvetica-Bold', font_size)
                text_width = c.stringWidth(text, 'Helvetica-Bold', font_size)
                c.setFillColor(colors.white)
                c.rect(cx - text_width/2 - 1, cy - font_size*.38,
                       text_width + 2, font_size*1.04, fill=1, stroke=0)
                c.setFillColor(colors.HexColor(red if color == 'W' else '#171717'))
                c.drawCentredString(cx, cy - font_size*.32, text)

    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(destination), pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=18*mm, bottomMargin=18*mm,
                            title=root.get('GN', [''])[0], author='SGF 手记棋谱转换工具')
    title = root.get('GN', [''])[0].strip()
    story = [Paragraph(rich_text(title), title_style)] if title else []
    info = metadata(root, count)
    rows = [[paragraph(info[i]), paragraph(info[i+1]) if i+1 < len(info) else '']
            for i in range(0, len(info), 2)]
    # 按文字实际宽度居中整块信息，避免两列短文字在宽表格中显得偏左。
    # 表格宽度独立于棋盘的单页自适应缩放，缩小棋盘后仍保持视觉居中。
    info_widths = [
        max(40*mm, min(68*mm, max(
            (pdfmetrics.stringWidth(line, font, 9) for line in info[column::2]),
            default=0) + 12*mm))
        for column in range(2)
    ]
    table = Table(rows, colWidths=info_widths, hAlign='CENTER')
    # noinspection SpellCheckingInspection
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0),
                              ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                              ('TOPPADDING', (0, 0), (-1, -1), 1),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    story.append(table)
    board = Board()
    story += [Spacer(1, 4*mm), board, Spacer(1, 3*mm)]
    repeats = sorted((moves for moves in spots.values() if len(moves) > 1), key=lambda m: m[0][0])
    if repeats:
        story.append(RepeatBoxes(repeats))
    if passes:
        story.append(paragraph('停着：' + '，'.join(f'{n}（{"黑" if c == "B" else "白"}）' for n, c in passes)))
    # 先测量实际内容；优先保留字号和页边距，通过缩小棋盘容纳说明。
    available_width = doc.width - 12
    available_height = doc.height - 12
    total_height = sum(item.wrap(available_width, 100000)[1] +
                       item.getSpaceBefore() + item.getSpaceAfter() for item in story)
    overflow = max(0, total_height - available_height + 2)
    if overflow:
        board.width = board.height = board.height - overflow
        if board.height < 120*mm:
            raise ValueError('对局信息或重复落点过多，无法在保持可读字号的情况下排入一页')
    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawCentredString(A4[0]/2, 10*mm, f'第 {document.page} 页')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return count, len(repeats)


def convert(source: Path, output: Path | None = None):
    destination = output if output is not None else PROJECT_DIR / 'output' / (source.stem + '.pdf')
    if destination.suffix.lower() != '.pdf':
        destination = Path(str(destination) + '.pdf')
    if destination.resolve() == source.resolve():
        raise ValueError('输出文件不能覆盖输入棋谱')
    count, repeats = write_pdf(read_sgf(source), destination)
    print(f'{source.name}: {count} 手，{repeats} 处重复落点 -> {destination.resolve()}')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sgf', nargs='*', type=Path, help='SGF 文件；省略时批量转换代码目录和 inputs 目录的 SGF')
    parser.add_argument('-o', '--output', type=Path, help='输出 PDF 路径（仅限单个输入）')
    args = parser.parse_args()
    folders = [PROJECT_DIR, PROJECT_DIR / 'inputs']
    sources = args.sgf or sorted(p for folder in folders if folder.is_dir()
                                for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.sgf')
    if not sources:
        parser.error('代码目录和 inputs 目录没有 SGF 文件，请指定文件路径')
    if args.output and len(sources) != 1:
        parser.error('--output 仅支持一个输入文件')
    failed = 0
    for source in sources:
        try:
            convert(source, args.output)
        except (OSError, ValueError) as exc:
            failed += 1
            print(f'转换失败：{source}: {exc}', file=sys.stderr)
    if failed:
        parser.exit(1, f'{failed} 个文件转换失败，其余文件已继续处理。\n')


if __name__ == '__main__':
    try:
        import reportlab
    except ImportError:
        # 使用完整的自带 Python，避免不同版本的二进制依赖混用。
        bundled_python = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if bundled_python.is_file() and Path(sys.executable).resolve() != bundled_python.resolve():
            import subprocess
            sys.exit(subprocess.call([str(bundled_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
    main()
