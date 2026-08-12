import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/chat_provider.dart';

/// A strip above the conversation showing whatever is currently due.
///
/// Notifications are gone the moment they are swiped, so this is where you
/// find out what one said. It shows everything `GET /nudges` reports — push
/// and in-app alike — rather than some separate category, because after the
/// fact the distinction stops mattering.
///
/// Collapses to nothing when there is nothing due, which is the common case.
class NudgeBanner extends StatefulWidget {
  const NudgeBanner({super.key});

  @override
  State<NudgeBanner> createState() => _NudgeBannerState();
}

class _NudgeBannerState extends State<NudgeBanner> {
  late final AppLifecycleListener _lifecycle;

  @override
  void initState() {
    super.initState();
    // Resuming matters more than the first build: the app may have sat
    // backgrounded since before the evening slot fired.
    _lifecycle = AppLifecycleListener(onResume: _refresh);
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  void _refresh() {
    if (!mounted) return;
    context.read<ChatProvider>().refreshNudges();
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final nudges = context.watch<ChatProvider>().visibleNudges;
    if (nudges.isEmpty) return const SizedBox.shrink();

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (final nudge in nudges)
          _NudgeRow(
            kind: nudge.kind,
            message: nudge.message,
            priority: nudge.priority,
          ),
        const Divider(height: 1, color: kBorder),
      ],
    );
  }
}

class _NudgeRow extends StatelessWidget {
  const _NudgeRow({
    required this.kind,
    required this.message,
    required this.priority,
  });

  final String kind;
  final String message;
  final String priority;

  /// High-priority items get the error colour; the rest stay muted so a
  /// low-stakes reminder cannot look like something is wrong.
  Color get _accent => priority == 'high' ? kError : kPrimaryLight;

  IconData get _icon => switch (priority) {
        'high' => Icons.error_outline_rounded,
        'medium' => Icons.info_outline_rounded,
        _ => Icons.lightbulb_outline_rounded,
      };

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
      color: _accent.withOpacity(0.08),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(_icon, size: 16, color: _accent),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: kTextPrimary,
                fontSize: 12.5,
                height: 1.35,
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.close_rounded, size: 16, color: kTextMuted),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
            tooltip: 'Dismiss',
            onPressed: () => context.read<ChatProvider>().dismissNudge(kind),
          ),
        ],
      ),
    );
  }
}
