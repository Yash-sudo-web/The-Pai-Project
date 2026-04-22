import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/chat_provider.dart';
import '../widgets/message_bubble.dart';
import '../widgets/sidebar.dart';
import '../widgets/thinking_indicator.dart';
import '../widgets/voice_button.dart';
import 'settings_screen.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _inputController = TextEditingController();
  final _scrollController = ScrollController();
  final _focusNode = FocusNode();

  @override
  void initState() {
    super.initState();
    // Show settings if not configured yet
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final provider = context.read<ChatProvider>();
      if (!provider.isConfigured) {
        _openSettings();
      }
    });
  }

  @override
  void dispose() {
    _inputController.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _scrollToBottom({bool animate = true}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      final target = _scrollController.position.maxScrollExtent;
      if (animate) {
        _scrollController.animateTo(
          target,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      } else {
        _scrollController.jumpTo(target);
      }
    });
  }

  Future<void> _send() async {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;
    _inputController.clear();
    _focusNode.requestFocus();
    await context.read<ChatProvider>().sendCommand(text);
    _scrollToBottom();
  }

  void _openSettings() {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => const SettingsDialog(),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kBg,
      body: Row(
        children: [
          // ── Sidebar ──────────────────────────────────────────────────────
          AppSidebar(onSettingsTap: _openSettings),

          // ── Divider ──────────────────────────────────────────────────────
          const VerticalDivider(width: 1, thickness: 1, color: kBorder),

          // ── Main chat area ───────────────────────────────────────────────
          Expanded(
            child: Column(
              children: [
                _ChatHeader(onSettingsTap: _openSettings),
                const Divider(height: 1, thickness: 1, color: kBorder),
                Expanded(child: _MessageList(scrollController: _scrollController)),
                _ConfirmationBar(),
                _InputBar(
                  controller: _inputController,
                  focusNode: _focusNode,
                  onSend: _send,
                  onScrollToBottom: _scrollToBottom,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ── Chat header ──────────────────────────────────────────────────────────────

class _ChatHeader extends StatelessWidget {
  const _ChatHeader({required this.onSettingsTap});
  final VoidCallback onSettingsTap;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    return Container(
      height: 56,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      color: kSurface,
      child: Row(
        children: [
          const Text(
            'Chat',
            style: TextStyle(
              color: kTextPrimary,
              fontSize: 16,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(width: 12),
          // Online indicator
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: provider.isOnline ? kSuccess : kError,
              boxShadow: [
                BoxShadow(
                  color: (provider.isOnline ? kSuccess : kError).withOpacity(0.4),
                  blurRadius: 6,
                ),
              ],
            ),
          ).animate(onPlay: (c) => c.repeat(reverse: true))
              .scaleXY(end: 1.3, duration: 1200.ms, curve: Curves.easeInOut),
          const Spacer(),
          // Clear chat
          IconButton(
            tooltip: 'Clear chat',
            icon: const Icon(Icons.delete_outline_rounded, size: 20),
            color: kTextSecondary,
            onPressed: provider.clearChat,
          ),
          // Load history
          IconButton(
            tooltip: 'Load today\'s history',
            icon: const Icon(Icons.history_rounded, size: 20),
            color: kTextSecondary,
            onPressed: () async {
              await context.read<ChatProvider>().loadHistory();
            },
          ),
        ],
      ),
    );
  }
}

// ── Message list ─────────────────────────────────────────────────────────────

class _MessageList extends StatelessWidget {
  const _MessageList({required this.scrollController});
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    final messages = provider.messages;
    final isThinking = provider.isThinking;

    if (messages.isEmpty && !isThinking) {
      return _EmptyState();
    }

    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
      itemCount: messages.length + (isThinking ? 1 : 0),
      itemBuilder: (context, index) {
        if (index == messages.length) {
          return const Padding(
            padding: EdgeInsets.only(top: 8),
            child: ThinkingIndicator(),
          );
        }
        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: MessageBubble(message: messages[index]),
        );
      },
    );
  }
}

class _EmptyState extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const LinearGradient(
                colors: [kPrimary, Color(0xFF4F1D96)],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              boxShadow: [
                BoxShadow(
                  color: kPrimary.withOpacity(0.3),
                  blurRadius: 24,
                  spreadRadius: 4,
                ),
              ],
            ),
            child: const Icon(Icons.auto_awesome_rounded, color: Colors.white, size: 32),
          ).animate().scaleXY(begin: 0.8, duration: 600.ms, curve: Curves.elasticOut),
          const SizedBox(height: 20),
          const Text(
            'Hello, I\'m PAI',
            style: TextStyle(color: kTextPrimary, fontSize: 22, fontWeight: FontWeight.w700),
          ).animate().fadeIn(delay: 200.ms, duration: 400.ms),
          const SizedBox(height: 8),
          const Text(
            'Your personal AI assistant.\nType a command or tap the mic to speak.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kTextSecondary, fontSize: 14, height: 1.6),
          ).animate().fadeIn(delay: 350.ms, duration: 400.ms),
        ],
      ),
    );
  }
}

// ── Confirmation bar ──────────────────────────────────────────────────────────

class _ConfirmationBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    if (!provider.hasPendingConfirmation) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.fromLTRB(24, 0, 24, 12),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: kSurfaceVar,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFF59E0B).withOpacity(0.5)),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_amber_rounded, color: Color(0xFFF59E0B), size: 18),
          const SizedBox(width: 10),
          const Expanded(
            child: Text(
              'This action needs your confirmation',
              style: TextStyle(color: kTextPrimary, fontSize: 13),
            ),
          ),
          const SizedBox(width: 12),
          TextButton(
            onPressed: provider.rejectAction,
            style: TextButton.styleFrom(foregroundColor: kError),
            child: const Text('Reject'),
          ),
          const SizedBox(width: 8),
          ElevatedButton(
            onPressed: provider.approveAction,
            style: ElevatedButton.styleFrom(
              backgroundColor: kPrimary,
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            ),
            child: const Text('Approve'),
          ),
        ],
      ),
    ).animate().slideY(begin: 0.3, duration: 300.ms, curve: Curves.easeOut).fadeIn();
  }
}

// ── Input bar ────────────────────────────────────────────────────────────────

class _InputBar extends StatefulWidget {
  const _InputBar({
    required this.controller,
    required this.focusNode,
    required this.onSend,
    required this.onScrollToBottom,
  });

  final TextEditingController controller;
  final FocusNode focusNode;
  final VoidCallback onSend;
  final VoidCallback onScrollToBottom;

  @override
  State<_InputBar> createState() => _InputBarState();
}

class _InputBarState extends State<_InputBar> {
  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    final isRecording = provider.isRecording;
    final transcript = provider.liveTranscript;

    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 16),
      decoration: const BoxDecoration(
        color: kSurface,
        border: Border(top: BorderSide(color: kBorder)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Live transcript preview while recording
          if (isRecording && transcript.isNotEmpty)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              margin: const EdgeInsets.only(bottom: 10),
              decoration: BoxDecoration(
                color: kPrimary.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: kPrimary.withOpacity(0.3)),
              ),
              child: Text(
                transcript,
                style: const TextStyle(color: kPrimaryLight, fontSize: 13),
              ),
            ).animate().fadeIn(duration: 200.ms),

          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              // Voice button
              VoiceButton(
                isRecording: isRecording,
                isDisabled: provider.isThinking,
                onTap: () {
                  if (isRecording) {
                    provider.stopRecordingAndSend();
                  } else {
                    provider.startRecording();
                  }
                  widget.onScrollToBottom();
                },
              ),
              const SizedBox(width: 12),
              // Text field
              Expanded(
                child: TextField(
                  controller: widget.controller,
                  focusNode: widget.focusNode,
                  enabled: !provider.isThinking && !isRecording,
                  maxLines: 5,
                  minLines: 1,
                  textInputAction: TextInputAction.newline,
                  style: const TextStyle(color: kTextPrimary, fontSize: 14),
                  decoration: InputDecoration(
                    hintText: isRecording
                        ? 'Listening…'
                        : provider.isThinking
                            ? 'PAI is thinking…'
                            : 'Send a command…',
                  ),
                  onSubmitted: (_) => widget.onSend(),
                ),
              ),
              const SizedBox(width: 12),
              // Send button
              _SendButton(
                enabled: !provider.isThinking && !isRecording,
                onTap: widget.onSend,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SendButton extends StatelessWidget {
  const _SendButton({required this.enabled, required this.onTap});
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        width: 44,
        height: 44,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: enabled
              ? const LinearGradient(
                  colors: [kPrimary, Color(0xFF5B21B6)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                )
              : null,
          color: enabled ? null : kSurfaceVar,
        ),
        child: Icon(
          Icons.arrow_upward_rounded,
          color: enabled ? Colors.white : kTextMuted,
          size: 20,
        ),
      ),
    );
  }
}
