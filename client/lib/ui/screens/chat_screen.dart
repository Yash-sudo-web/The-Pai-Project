import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/chat_provider.dart';
import '../widgets/message_bubble.dart';
import '../widgets/thinking_indicator.dart';
import 'chat_history_panel.dart';
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
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final provider = context.read<ChatProvider>();
      if (!provider.isConfigured) _openSettings();
      // Listen for transcribed text from voice
      provider.addListener(_onProviderChange);
    });
  }

  void _onProviderChange() {
    final provider = context.read<ChatProvider>();
    final text = provider.transcribedText;
    if (text != null) {
      _inputController.text = text;
      _inputController.selection =
          TextSelection.collapsed(offset: text.length);
      provider.consumeTranscribedText();
      _focusNode.requestFocus();
    }
  }

  @override
  void dispose() {
    context.read<ChatProvider>().removeListener(_onProviderChange);
    _inputController.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
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

  String _greeting() {
    final hour = DateTime.now().hour;
    if (hour < 12) return 'Good Morning.';
    if (hour < 17) return 'Good Afternoon.';
    return 'Good Evening.';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kBg,
      body: Row(
        children: [
          // ── Left Sidebar ──────────────────────────────────────────────
          _LeftSidebar(onSettingsTap: _openSettings),

          // ── Center: Mic & Greeting ────────────────────────────────────
          Expanded(
            flex: 3,
            child: _CenterArea(greeting: _greeting()),
          ),

          // ── Right: Chat Panel ─────────────────────────────────────────
          SizedBox(
            width: 360,
            child: _ChatPanel(
              inputController: _inputController,
              scrollController: _scrollController,
              focusNode: _focusNode,
              onSend: _send,
            ),
          ),
        ],
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// LEFT SIDEBAR
// ═══════════════════════════════════════════════════════════════════════════════

class _LeftSidebar extends StatelessWidget {
  const _LeftSidebar({required this.onSettingsTap});
  final VoidCallback onSettingsTap;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();

    return Container(
      width: 200,
      color: kSurface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Logo
          Padding(
            padding: const EdgeInsets.all(20),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: const LinearGradient(
                      colors: [kAccent, kPrimary],
                    ),
                  ),
                  child: const Icon(Icons.auto_awesome, color: Colors.white, size: 16),
                ),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    'the PAI\nProject',
                    style: TextStyle(
                      color: kTextPrimary,
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                      height: 1.2,
                    ),
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 8),

          // Nav items
          _NavItem(
            icon: Icons.dashboard_rounded,
            label: 'Dashboard',
            selected: true,
          ),
          _NavItem(
            icon: Icons.history_rounded,
            label: 'Chat History',
            onTap: () {
              showDialog(
                context: context,
                barrierColor: Colors.transparent,
                builder: (_) => const ChatHistoryPanel(),
              );
            },
          ),
          _NavItem(
            icon: Icons.delete_outline_rounded,
            label: 'Clear Chat',
            onTap: () => provider.clearChat(),
          ),
          _NavItem(
            icon: Icons.settings_rounded,
            label: 'Settings',
            onTap: onSettingsTap,
          ),

          const Spacer(),

          // TTS toggle
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                const Icon(Icons.volume_up_rounded, color: kTextMuted, size: 16),
                const SizedBox(width: 8),
                const Text('TTS', style: TextStyle(color: kTextMuted, fontSize: 12)),
                const Spacer(),
                Switch(
                  value: provider.ttsEnabled,
                  onChanged: provider.setTtsEnabled,
                  activeColor: kAccent,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ],
            ),
          ),

          // Status
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: provider.isOnline ? kSuccess : kError,
                    boxShadow: [
                      BoxShadow(
                        color: (provider.isOnline ? kSuccess : kError)
                            .withOpacity(0.5),
                        blurRadius: 6,
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  provider.isOnline ? 'System Ready' : 'Offline',
                  style: TextStyle(
                    color: provider.isOnline ? kSuccess : kError,
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    this.selected = false,
    this.onTap,
  });
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      child: Material(
        color: selected ? kPrimary.withOpacity(0.15) : Colors.transparent,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(8),
          hoverColor: kSurfaceVar,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(icon,
                    size: 18,
                    color: selected ? kAccent : kTextSecondary),
                const SizedBox(width: 10),
                Text(
                  label,
                  style: TextStyle(
                    color: selected ? kAccent : kTextSecondary,
                    fontSize: 13,
                    fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// CENTER AREA — Greeting + Mic Button
// ═══════════════════════════════════════════════════════════════════════════════

class _CenterArea extends StatelessWidget {
  const _CenterArea({required this.greeting});
  final String greeting;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: kBg,
      child: Column(
        children: [
          const SizedBox(height: 48),
          // Greeting
          Text(
            greeting,
            style: const TextStyle(
              color: kTextPrimary,
              fontSize: 32,
              fontWeight: FontWeight.w700,
            ),
          ).animate().fadeIn(duration: 600.ms).slideY(begin: -0.1),

          const Spacer(),

          // Mic button
          const _MicButton(),

          const Spacer(),
          const SizedBox(height: 48),
        ],
      ),
    );
  }
}

class _MicButton extends StatefulWidget {
  const _MicButton();

  @override
  State<_MicButton> createState() => _MicButtonState();
}

class _MicButtonState extends State<_MicButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    final voiceState = provider.voiceState;

    // Drive pulse animation
    if (voiceState == VoiceState.recording) {
      _pulseController.repeat(reverse: true);
    } else {
      _pulseController.stop();
      _pulseController.reset();
    }

    return GestureDetector(
      onTap: provider.isThinking ? null : () => provider.toggleRecording(),
      child: AnimatedBuilder(
        animation: _pulseController,
        builder: (context, child) {
          final pulseScale = voiceState == VoiceState.recording
              ? 1.0 + (_pulseController.value * 0.15)
              : 1.0;
          final glowOpacity = voiceState == VoiceState.recording
              ? 0.3 + (_pulseController.value * 0.4)
              : 0.15;

          return Transform.scale(
            scale: pulseScale,
            child: Container(
              width: 140,
              height: 140,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: voiceState == VoiceState.recording
                      ? kAccent
                      : voiceState == VoiceState.transcribing
                          ? kPrimary
                          : kAccent.withOpacity(0.4),
                  width: 2.5,
                ),
                boxShadow: [
                  BoxShadow(
                    color: (voiceState == VoiceState.recording
                            ? kAccent
                            : kPrimary)
                        .withOpacity(glowOpacity),
                    blurRadius: voiceState == VoiceState.recording ? 40 : 20,
                    spreadRadius:
                        voiceState == VoiceState.recording ? 8 : 2,
                  ),
                ],
              ),
              child: Center(
                child: voiceState == VoiceState.transcribing
                    ? const SizedBox(
                        width: 36,
                        height: 36,
                        child: CircularProgressIndicator(
                          strokeWidth: 3,
                          color: kAccent,
                        ),
                      )
                    : Icon(
                        voiceState == VoiceState.recording
                            ? Icons.stop_rounded
                            : Icons.mic_rounded,
                        color: voiceState == VoiceState.recording
                            ? kAccent
                            : kAccent.withOpacity(0.8),
                        size: 48,
                      ),
              ),
            ),
          );
        },
      ),
    );
  }
}

// Wrapper because AnimatedBuilder is a typedef for AnimatedWidget
class AnimatedBuilder extends AnimatedWidget {
  const AnimatedBuilder({
    super.key,
    required Animation<double> animation,
    required this.builder,
  }) : super(listenable: animation);

  final Widget Function(BuildContext context, Widget? child) builder;

  @override
  Widget build(BuildContext context) => builder(context, null);
}

// ═══════════════════════════════════════════════════════════════════════════════
// RIGHT CHAT PANEL
// ═══════════════════════════════════════════════════════════════════════════════

class _ChatPanel extends StatelessWidget {
  const _ChatPanel({
    required this.inputController,
    required this.scrollController,
    required this.focusNode,
    required this.onSend,
  });
  final TextEditingController inputController;
  final ScrollController scrollController;
  final FocusNode focusNode;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: kSurface,
        border: Border(left: BorderSide(color: kBorder)),
      ),
      child: Column(
        children: [
          // Header
          _PanelHeader(),
          const Divider(height: 1, color: kBorder),

          // Messages
          Expanded(child: _MessageArea(scrollController: scrollController)),

          // Confirmation bar
          _ConfirmationBar(),

          // Input
          _ChatInput(
            controller: inputController,
            focusNode: focusNode,
            onSend: onSend,
          ),
        ],
      ),
    );
  }
}

class _PanelHeader extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final dateStr =
        '${now.day.toString().padLeft(2, '0')}/${now.month.toString().padLeft(2, '0')}/${now.year} | '
        '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}';

    return Container(
      height: 56,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      child: Row(
        children: [
          const Text(
            'Interaction Log',
            style: TextStyle(
              color: kTextPrimary,
              fontSize: 15,
              fontWeight: FontWeight.w600,
            ),
          ),
          const Spacer(),
          Text(
            dateStr,
            style: const TextStyle(color: kTextMuted, fontSize: 11),
          ),
        ],
      ),
    );
  }
}

class _MessageArea extends StatelessWidget {
  const _MessageArea({required this.scrollController});
  final ScrollController scrollController;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    final messages = provider.messages;
    final isThinking = provider.isThinking;

    if (messages.isEmpty && !isThinking) {
      return Center(
        child: Text(
          'No messages yet.\nSpeak or type to begin.',
          textAlign: TextAlign.center,
          style: TextStyle(color: kTextMuted, fontSize: 13, height: 1.6),
        ).animate().fadeIn(duration: 400.ms),
      );
    }

    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      itemCount: messages.length + (isThinking ? 1 : 0),
      itemBuilder: (context, index) {
        if (index == messages.length) {
          return const Padding(
            padding: EdgeInsets.only(top: 8),
            child: ThinkingIndicator(),
          );
        }
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: MessageBubble(message: messages[index]),
        );
      },
    );
  }
}

class _ConfirmationBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    if (!provider.hasPendingConfirmation) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: kSurfaceVar,
        borderRadius: BorderRadius.circular(10),
        border:
            Border.all(color: const Color(0xFFF59E0B).withOpacity(0.5)),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_amber_rounded,
              color: Color(0xFFF59E0B), size: 16),
          const SizedBox(width: 8),
          const Expanded(
            child: Text('Confirm this action?',
                style: TextStyle(color: kTextPrimary, fontSize: 12)),
          ),
          TextButton(
            onPressed: provider.rejectAction,
            style: TextButton.styleFrom(
                foregroundColor: kError,
                padding: const EdgeInsets.symmetric(horizontal: 10)),
            child: const Text('Reject', style: TextStyle(fontSize: 12)),
          ),
          ElevatedButton(
            onPressed: provider.approveAction,
            style: ElevatedButton.styleFrom(
              backgroundColor: kPrimary,
              foregroundColor: Colors.white,
              padding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
            child: const Text('Approve', style: TextStyle(fontSize: 12)),
          ),
        ],
      ),
    ).animate().slideY(begin: 0.3, duration: 300.ms).fadeIn();
  }
}

class _ChatInput extends StatelessWidget {
  const _ChatInput({
    required this.controller,
    required this.focusNode,
    required this.onSend,
  });
  final TextEditingController controller;
  final FocusNode focusNode;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: kBorder)),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              enabled: !provider.isThinking && !provider.isTranscribing,
              maxLines: 3,
              minLines: 1,
              style: const TextStyle(color: kTextPrimary, fontSize: 13),
              decoration: InputDecoration(
                hintText: provider.isTranscribing
                    ? 'Transcribing…'
                    : 'ask pai anything... /cmds',
                contentPadding: const EdgeInsets.symmetric(
                    horizontal: 14, vertical: 10),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: const BorderSide(color: kBorder),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: const BorderSide(color: kBorder),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: const BorderSide(color: kAccent, width: 1.5),
                ),
              ),
              onSubmitted: (_) => onSend(),
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: onSend,
            child: Container(
              width: 38,
              height: 38,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(10),
                gradient: const LinearGradient(
                  colors: [kPrimary, Color(0xFF5B21B6)],
                ),
              ),
              child: const Icon(Icons.send_rounded,
                  color: Colors.white, size: 18),
            ),
          ),
        ],
      ),
    );
  }
}
