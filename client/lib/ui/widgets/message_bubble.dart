import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../app.dart';
import '../../models/chat_message.dart';

class MessageBubble extends StatefulWidget {
  const MessageBubble({super.key, required this.message});
  final ChatMessage message;

  @override
  State<MessageBubble> createState() => _MessageBubbleState();
}

class _MessageBubbleState extends State<MessageBubble> {
  bool _hovered = false;

  bool get _isUser => widget.message.role == MessageRole.user;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Row(
        mainAxisAlignment:
            _isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!_isUser) _Avatar(),
          if (!_isUser) const SizedBox(width: 10),
          Flexible(
            child: Column(
              crossAxisAlignment: _isUser
                  ? CrossAxisAlignment.end
                  : CrossAxisAlignment.start,
              children: [
                _Bubble(
                  message: widget.message,
                  isUser: _isUser,
                ),
                if (_hovered) _Toolbar(message: widget.message, isUser: _isUser),
              ],
            ),
          ),
          if (_isUser) const SizedBox(width: 10),
          if (_isUser) _UserAvatar(),
        ],
      ),
    ).animate().fadeIn(duration: 300.ms).slideY(begin: 0.1, duration: 300.ms);
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
  const _Bubble({required this.message, required this.isUser});
  final ChatMessage message;
  final bool isUser;

  @override
  Widget build(BuildContext context) {
    if (isUser) {
      return Container(
        constraints: const BoxConstraints(maxWidth: 560),
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
        child: Text(
          message.text,
          style: const TextStyle(color: Colors.white, fontSize: 14, height: 1.55),
        ),
      );
    }

    // AI message
    return Container(
      constraints: const BoxConstraints(maxWidth: 660),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: kSurface,
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
      child: Text(
        message.text,
        style: TextStyle(
          color: message.isError ? kError : kTextPrimary,
          fontSize: 14,
          height: 1.65,
        ),
      ),
    );
  }
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({required this.message, required this.isUser});
  final ChatMessage message;
  final bool isUser;

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
            onTap: () {
              Clipboard.setData(ClipboardData(text: message.text));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('Copied to clipboard'),
                  duration: Duration(seconds: 1),
                  backgroundColor: kSurfaceVar,
                ),
              );
            },
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
