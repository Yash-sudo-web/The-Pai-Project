import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../app.dart';
import '../../models/chat_message.dart';
import 'markdown_text.dart';

class MessageBubble extends StatefulWidget {
  const MessageBubble({
    super.key,
    required this.message,
    this.isStreaming = false,
  });

  final ChatMessage message;

  /// Draws a caret after the text while tokens are still arriving.
  final bool isStreaming;

  @override
  State<MessageBubble> createState() => _MessageBubbleState();
}

class _MessageBubbleState extends State<MessageBubble> {
  bool _hovered = false;
  bool _pinnedOpen = false;

  bool get _isUser => widget.message.role == MessageRole.user;

  @override
  Widget build(BuildContext context) {
    // Touch platforms have no hover, so a hover-only toolbar would put copy
    // and timestamps permanently out of reach. There, a tap toggles it.
    final isTouch = switch (Theme.of(context).platform) {
      TargetPlatform.iOS || TargetPlatform.android => true,
      _ => false,
    };
    final showToolbar = isTouch ? _pinnedOpen : (_hovered || _pinnedOpen);

    final content = Row(
      mainAxisAlignment:
          _isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (!_isUser) _Avatar(),
        if (!_isUser) const SizedBox(width: 10),
        Flexible(
          child: Column(
            crossAxisAlignment:
                _isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
            children: [
              GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: isTouch
                    ? () => setState(() => _pinnedOpen = !_pinnedOpen)
                    : null,
                onLongPress: () => _copy(context),
                child: _Bubble(
                  message: widget.message,
                  isUser: _isUser,
                  isStreaming: widget.isStreaming,
                ),
              ),
              if (showToolbar)
                _Toolbar(message: widget.message, onCopy: () => _copy(context)),
            ],
          ),
        ),
        if (_isUser) const SizedBox(width: 10),
        if (_isUser) _UserAvatar(),
      ],
    );

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: content,
    ).animate().fadeIn(duration: 220.ms).slideY(begin: 0.06, duration: 220.ms);
  }

  void _copy(BuildContext context) {
    Clipboard.setData(ClipboardData(text: widget.message.text));
    HapticFeedback.selectionClick();
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: const Text('Copied'),
          duration: const Duration(milliseconds: 1200),
          backgroundColor: kSurfaceVar,
          behavior: SnackBarBehavior.floating,
          width: 140,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
        ),
      );
  }
}

class _Avatar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 32,
      height: 32,
      margin: const EdgeInsets.only(top: 4),
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: const LinearGradient(
          colors: [kPrimary, Color(0xFF4C1D95)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        boxShadow: [BoxShadow(color: kPrimary.withOpacity(0.3), blurRadius: 8)],
      ),
      child: const Icon(Icons.auto_awesome_rounded,
          color: Colors.white, size: 15),
    );
  }
}

class _UserAvatar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 32,
      height: 32,
      margin: const EdgeInsets.only(top: 4),
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: kSurfaceVar,
        border: Border.all(color: kBorder),
      ),
      child: const Icon(Icons.person_rounded, color: kTextSecondary, size: 16),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.message,
    required this.isUser,
    this.isStreaming = false,
  });
  final ChatMessage message;
  final bool isUser;
  final bool isStreaming;

  @override
  Widget build(BuildContext context) {
    // Cap by viewport share as well as absolute width so bubbles stay
    // readable on a phone and don't sprawl on a wide desktop panel.
    final maxWidth = MediaQuery.of(context).size.width * 0.82;

    if (isUser) {
      return Container(
        constraints: BoxConstraints(maxWidth: maxWidth.clamp(200.0, 560.0)),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [kPrimary, Color(0xFF5B21B6)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(16),
            topRight: Radius.circular(4),
            bottomLeft: Radius.circular(16),
            bottomRight: Radius.circular(16),
          ),
          boxShadow: [
            BoxShadow(color: kPrimary.withOpacity(0.2), blurRadius: 12),
          ],
        ),
        // What the user typed is literal — never reinterpret it as markup.
        child: Text(
          message.text,
          style: const TextStyle(color: Colors.white, fontSize: 14, height: 1.55),
        ),
      );
    }

    final textStyle = TextStyle(
      color: message.isError ? kError : kTextPrimary,
      fontSize: 14,
      height: 1.65,
    );

    return Container(
      constraints: BoxConstraints(maxWidth: maxWidth.clamp(240.0, 660.0)),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: message.isError ? kError.withOpacity(0.06) : kSurface,
        borderRadius: const BorderRadius.only(
          topLeft: Radius.circular(4),
          topRight: Radius.circular(16),
          bottomLeft: Radius.circular(16),
          bottomRight: Radius.circular(16),
        ),
        border: Border.all(
          color: message.isError ? kError.withOpacity(0.4) : kBorder,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Errors are plain strings from the server, not markdown.
          if (message.isError)
            Text(message.text, style: textStyle)
          else
            MarkdownText(
              message.text,
              baseStyle: textStyle,
              compact: message.text.length < 160,
            ),
          if (isStreaming) const _StreamingCaret(),
        ],
      ),
    );
  }
}

/// A blinking caret shown while tokens are still arriving.
class _StreamingCaret extends StatelessWidget {
  const _StreamingCaret();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Container(
        width: 7,
        height: 14,
        decoration: BoxDecoration(
          color: kPrimaryLight,
          borderRadius: BorderRadius.circular(2),
        ),
      )
          .animate(onPlay: (c) => c.repeat(reverse: true))
          .fadeIn(duration: 520.ms),
    );
  }
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({required this.message, required this.onCopy});
  final ChatMessage message;
  final VoidCallback onCopy;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            _formatTime(message.timestamp),
            style: const TextStyle(color: kTextMuted, fontSize: 11),
          ),
          const SizedBox(width: 8),
          _ToolbarBtn(
            icon: Icons.copy_rounded,
            tooltip: 'Copy',
            onTap: onCopy,
          ),
        ],
      ),
    ).animate().fadeIn(duration: 150.ms);
  }

  String _formatTime(DateTime dt) {
    final h = dt.hour.toString().padLeft(2, '0');
    final m = dt.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }
}

class _ToolbarBtn extends StatelessWidget {
  const _ToolbarBtn({
    required this.icon,
    required this.tooltip,
    required this.onTap,
  });
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        borderRadius: BorderRadius.circular(6),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(icon, size: 14, color: kTextMuted),
        ),
      ),
    );
  }
}
