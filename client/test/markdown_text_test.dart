import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pai_client/ui/widgets/markdown_text.dart';

/// Collects every glyph the widget tree would actually paint.
String renderedText(WidgetTester tester) {
  final buffer = StringBuffer();
  for (final element in find.byType(RichText).evaluate()) {
    final widget = element.widget as RichText;
    buffer.write(widget.text.toPlainText());
    buffer.write('\n');
  }
  return buffer.toString();
}

Future<void> pumpMarkdown(WidgetTester tester, String source) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: MarkdownText(
            source,
            baseStyle: const TextStyle(fontSize: 14),
          ),
        ),
      ),
    ),
  );
}

void main() {
  group('inline formatting', () {
    testWidgets('bold markers are consumed, not printed', (tester) async {
      await pumpMarkdown(tester, 'Logged **bench press** today.');
      final text = renderedText(tester);

      expect(text, contains('bench press'));
      expect(text, isNot(contains('**')),
          reason: 'asterisks must not reach the screen');
    });

    testWidgets('italic and inline code are unwrapped', (tester) async {
      await pumpMarkdown(tester, 'Use *care* with `nutrition.log_meal` here.');
      final text = renderedText(tester);

      expect(text, contains('care'));
      expect(text, contains('nutrition.log_meal'));
      expect(text, isNot(contains('`')));
      expect(text, isNot(contains('*care*')));
    });

    testWidgets('underscores in identifiers survive intact', (tester) async {
      // Underscore emphasis is unsupported precisely so these keep their shape.
      await pumpMarkdown(tester, 'Call nutrition.log_meal from __init__ here.');
      final text = renderedText(tester);

      expect(text, contains('nutrition.log_meal'));
      expect(text, contains('__init__'));
    });

    testWidgets('bold is styled, not merely stripped', (tester) async {
      await pumpMarkdown(tester, 'a **b** c');
      final richText =
          tester.widget<RichText>(find.byType(RichText).first);

      var sawBold = false;
      richText.text.visitChildren((span) {
        if (span is TextSpan &&
            span.text == 'b' &&
            span.style?.fontWeight == FontWeight.w700) {
          sawBold = true;
        }
        return true;
      });
      expect(sawBold, isTrue);
    });

    testWidgets('unmatched markers are left alone', (tester) async {
      await pumpMarkdown(tester, '2 * 3 = 6 and a lone ** here');
      expect(renderedText(tester), contains('2 * 3 = 6'));
    });
  });

  group('block structure', () {
    testWidgets('bullets get a marker and drop the dash', (tester) async {
      await pumpMarkdown(tester, '- eggs\n- toast');
      final text = renderedText(tester);

      expect(text, contains('eggs'));
      expect(text, contains('toast'));
      expect(text, contains('•'));
      expect(text, isNot(contains('- eggs')));
    });

    testWidgets('numbered lists keep their numbers', (tester) async {
      await pumpMarkdown(tester, '1. warm up\n2. squat');
      final text = renderedText(tester);

      expect(text, contains('1.'));
      expect(text, contains('2.'));
      expect(text, contains('warm up'));
    });

    testWidgets('headings render without hashes', (tester) async {
      await pumpMarkdown(tester, '## Today\nsome text');
      final text = renderedText(tester);

      expect(text, contains('Today'));
      expect(text, isNot(contains('#')));
    });

    testWidgets('fenced code keeps its line breaks', (tester) async {
      await pumpMarkdown(tester, 'before\n```\nline one\nline two\n```\nafter');
      final text = renderedText(tester);

      expect(text, contains('line one\nline two'));
      expect(text, isNot(contains('```')));
      expect(text, contains('before'));
      expect(text, contains('after'));
    });

    testWidgets('consecutive lines join into one paragraph', (tester) async {
      await pumpMarkdown(tester, 'one\ntwo\n\nthree');
      final text = renderedText(tester);

      expect(text, contains('one two'));
      expect(text, contains('three'));
    });
  });

  group('robustness', () {
    testWidgets('empty input does not throw', (tester) async {
      await pumpMarkdown(tester, '');
      expect(tester.takeException(), isNull);
    });

    testWidgets('unterminated code fence does not throw', (tester) async {
      await pumpMarkdown(tester, 'text\n```\nnever closed');
      expect(tester.takeException(), isNull);
      expect(renderedText(tester), contains('never closed'));
    });

    testWidgets('a realistic assistant reply renders cleanly', (tester) async {
      await pumpMarkdown(tester, '''
Got it — I logged your breakfast.

**What I recorded:**
- 4 × bread slices (~280 kcal)
- 2 eggs (~155 kcal)

### Totals
1. Calories: **435**
2. Protein: 18g

Let me know if that looks off.
''');
      final text = renderedText(tester);

      expect(tester.takeException(), isNull);
      expect(text, isNot(contains('**')));
      expect(text, isNot(contains('###')));
      expect(text, contains('What I recorded:'));
      expect(text, contains('435'));
      expect(text, contains('Totals'));
      expect(text, contains('•'));
    });
  });
}
