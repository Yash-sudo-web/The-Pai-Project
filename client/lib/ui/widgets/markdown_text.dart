import 'package:flutter/material.dart';

import '../../app.dart';

/// Renders the subset of Markdown the assistant actually produces.
///
/// A dedicated Markdown package would handle more syntax, but everything the
/// model emits here is headings, bullets, numbered steps, bold/italic, and
/// code — and a small local renderer keeps full control over spacing and
/// colour, with no dependency to break at build time.
///
/// Uses [Text.rich] rather than [SelectableText] so an enclosing
/// [SelectionArea] can select across several messages at once.
class MarkdownText extends StatelessWidget {
  const MarkdownText(
    this.data, {
    super.key,
    required this.baseStyle,
    this.compact = false,
  });

  final String data;
  final TextStyle baseStyle;

  /// Tightens vertical rhythm for short, single-paragraph replies.
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final blocks = _parseBlocks(data);
    if (blocks.isEmpty) return Text('', style: baseStyle);

    final children = <Widget>[];
    for (var i = 0; i < blocks.length; i++) {
      if (i > 0) {
        children.add(SizedBox(height: blocks[i].spacingBefore(compact)));
      }
      children.add(blocks[i].build(baseStyle));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: children,
    );
  }
}

// ---------------------------------------------------------------------------
// Block model
// ---------------------------------------------------------------------------

abstract class _Block {
  double spacingBefore(bool compact) => compact ? 6 : 10;
  Widget build(TextStyle base);
}

class _Paragraph extends _Block {
  _Paragraph(this.text);
  final String text;

  @override
  Widget build(TextStyle base) => Text.rich(_inline(text, base), style: base);
}

class _Heading extends _Block {
  _Heading(this.text, this.level);
  final String text;
  final int level;

  @override
  double spacingBefore(bool compact) => compact ? 10 : 14;

  @override
  Widget build(TextStyle base) {
    final size = switch (level) { 1 => 4.0, 2 => 2.0, _ => 1.0 };
    return Text.rich(
      _inline(
        text,
        base.copyWith(
          fontSize: (base.fontSize ?? 14) + size,
          fontWeight: FontWeight.w700,
          height: 1.35,
        ),
      ),
    );
  }
}

class _ListItem extends _Block {
  _ListItem(this.text, {required this.marker, required this.indent});
  final String text;
  final String marker;
  final int indent;

  @override
  double spacingBefore(bool compact) => 4;

  @override
  Widget build(TextStyle base) {
    return Padding(
      padding: EdgeInsets.only(left: 2.0 + indent * 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: marker.length > 1 ? 22 : 16,
            child: Text(
              marker,
              style: base.copyWith(
                color: kPrimaryLight,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Expanded(child: Text.rich(_inline(text, base), style: base)),
        ],
      ),
    );
  }
}

class _CodeBlock extends _Block {
  _CodeBlock(this.code);
  final String code;

  @override
  Widget build(TextStyle base) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: kBg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: kBorder),
      ),
      // Long lines scroll rather than forcing the bubble wider.
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Text(
          code,
          style: base.copyWith(
            fontFamily: 'monospace',
            fontSize: (base.fontSize ?? 14) - 1,
            height: 1.5,
          ),
        ),
      ),
    );
  }
}

class _Divider extends _Block {
  @override
  Widget build(TextStyle base) =>
      const Divider(height: 1, thickness: 1, color: kBorder);
}

// ---------------------------------------------------------------------------
// Block parsing
// ---------------------------------------------------------------------------

final _headingRe = RegExp(r'^(#{1,4})\s+(.*)$');
final _bulletRe = RegExp(r'^(\s*)[-*•]\s+(.*)$');
final _numberedRe = RegExp(r'^(\s*)(\d{1,2})[.)]\s+(.*)$');
final _dividerRe = RegExp(r'^\s*([-*_])\1{2,}\s*$');

List<_Block> _parseBlocks(String source) {
  final lines = source.replaceAll('\r\n', '\n').split('\n');
  final blocks = <_Block>[];
  final paragraph = <String>[];

  void flush() {
    if (paragraph.isEmpty) return;
    blocks.add(_Paragraph(paragraph.join(' ').trim()));
    paragraph.clear();
  }

  for (var i = 0; i < lines.length; i++) {
    final line = lines[i];

    // Fenced code: consume until the closing fence or end of input.
    if (line.trimLeft().startsWith('```')) {
      flush();
      final code = <String>[];
      i++;
      while (i < lines.length && !lines[i].trimLeft().startsWith('```')) {
        code.add(lines[i]);
        i++;
      }
      if (code.isNotEmpty) blocks.add(_CodeBlock(code.join('\n')));
      continue;
    }

    if (line.trim().isEmpty) {
      flush();
      continue;
    }

    if (_dividerRe.hasMatch(line)) {
      flush();
      blocks.add(_Divider());
      continue;
    }

    final heading = _headingRe.firstMatch(line);
    if (heading != null) {
      flush();
      blocks.add(_Heading(heading.group(2)!, heading.group(1)!.length));
      continue;
    }

    final numbered = _numberedRe.firstMatch(line);
    if (numbered != null) {
      flush();
      blocks.add(_ListItem(
        numbered.group(3)!,
        marker: '${numbered.group(2)}.',
        indent: numbered.group(1)!.length ~/ 2,
      ));
      continue;
    }

    final bullet = _bulletRe.firstMatch(line);
    if (bullet != null) {
      flush();
      blocks.add(_ListItem(
        bullet.group(2)!,
        marker: '•',
        indent: bullet.group(1)!.length ~/ 2,
      ));
      continue;
    }

    paragraph.add(line.trim());
  }

  flush();
  return blocks;
}

// ---------------------------------------------------------------------------
// Inline spans
// ---------------------------------------------------------------------------

// Ordered so code wins over emphasis, and ** over *.
//
// Each delimiter must hug non-whitespace, so arithmetic like "2 * 3 = 6" and
// a stray "**" are left as typed rather than swallowed as emphasis.
//
// Underscore emphasis is deliberately unsupported: the assistant emits
// identifiers such as nutrition.log_meal and __init__ far more often than it
// emits _italics_, and mangling those is the worse failure.
final _inlineRe = RegExp(
  r'(`[^`\n]+`)'
  r'|(\*\*(?:[^\s*][^*\n]*?[^\s*]|[^\s*])\*\*)'
  r'|(\*(?:[^\s*][^*\n]*?[^\s*]|[^\s*])\*)',
);

InlineSpan _inline(String text, TextStyle base) {
  final spans = <InlineSpan>[];
  var cursor = 0;

  for (final match in _inlineRe.allMatches(text)) {
    if (match.start > cursor) {
      spans.add(TextSpan(text: text.substring(cursor, match.start)));
    }
    final token = match.group(0)!;

    if (token.startsWith('`')) {
      spans.add(TextSpan(
        text: token.substring(1, token.length - 1),
        style: base.copyWith(
          fontFamily: 'monospace',
          fontSize: (base.fontSize ?? 14) - 1,
          color: kAccent,
          backgroundColor: kBg,
        ),
      ));
    } else if (token.startsWith('**')) {
      spans.add(TextSpan(
        text: token.substring(2, token.length - 2),
        style: base.copyWith(fontWeight: FontWeight.w700),
      ));
    } else {
      spans.add(TextSpan(
        text: token.substring(1, token.length - 1),
        style: base.copyWith(fontStyle: FontStyle.italic),
      ));
    }
    cursor = match.end;
  }

  if (cursor < text.length) {
    spans.add(TextSpan(text: text.substring(cursor)));
  }

  return TextSpan(style: base, children: spans);
}
