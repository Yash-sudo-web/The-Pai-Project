import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../models/chat_message.dart';
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

    // Follow the reply as tokens arrive — but only if the user is already at
    // the bottom, so scrolling back to re-read something isn't yanked away.
    if (provider.isStreaming && _isNearBottom) _stickToBottom();
  }

  bool get _isNearBottom {
    if (!_scrollController.hasClients) return true;
    final position = _scrollController.position;
    return position.maxScrollExtent - position.pixels < 140;
  }

  /// Jump without animating: token updates arrive faster than an animation
  /// can finish, and queued animations fight each other.
  void _stickToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
    });
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

  Future<void> _send([String? preset]) async {
    final text = (preset ?? _inputController.text).trim();
    if (text.isEmpty) return;
    _inputController.clear();
    // Keyboards steal the view on mobile; only refocus where it's free.
    if (MediaQuery.of(context).size.width >= 800) _focusNode.requestFocus();
    _scrollToBottom();
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
    final isDesktop = MediaQuery.of(context).size.width >= 800;

    return Scaffold(
      backgroundColor: kBg,
      drawer: isDesktop ? null : Drawer(child: _LeftSidebar(onSettingsTap: _openSettings)),
      appBar: isDesktop
          ? null
          : AppBar(
              backgroundColor: kSurface,
              iconTheme: const IconThemeData(color: kTextPrimary),
              title: Text(_greeting(), style: const TextStyle(color: kTextPrimary, fontSize: 16)),
              elevation: 0,
            ),
      body: isDesktop
          ? Row(
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
                  // Chat is the primary surface; a fixed 360 wasted a wide
                  // window and made long replies a column of short lines.
                  width: (MediaQuery.of(context).size.width * 0.40)
                      .clamp(360.0, 620.0),
                  child: _ChatPanel(
                    inputController: _inputController,
                    scrollController: _scrollController,
                    focusNode: _focusNode,
                    onSend: _send,
                    onSuggestion: _send,
                  ),
                ),
              ],
            )
          : SafeArea(
              child: Stack(
                children: [
                  _ChatPanel(
                    inputController: _inputController,
                    scrollController: _scrollController,
                    focusNode: _focusNode,
                    onSend: _send,
                    onSuggestion: _send,
                  ),
                  Positioned(
                    bottom: 85,
                    right: 16,
                    child: Transform.scale(
                      scale: 0.6,
                      child: const _MicButton(),
                    ),
                  ),
                ],
              ),
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
    this.onSuggestion,
  });
  final TextEditingController inputController;
  final ScrollController scrollController;
  final FocusNode focusNode;
  final VoidCallback onSend;
  final void Function(String)? onSuggestion;

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
          Expanded(
            child: _MessageArea(
              scrollController: scrollController,
              onSuggestion: onSuggestion,
            ),
          ),

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

class _MessageArea extends StatefulWidget {
  const _MessageArea({required this.scrollController, this.onSuggestion});
  final ScrollController scrollController;
  final void Function(String)? onSuggestion;

  @override
  State<_MessageArea> createState() => _MessageAreaState();
}

class _MessageAreaState extends State<_MessageArea> {
  bool _showJumpButton = false;

  @override
  void initState() {
    super.initState();
    widget.scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    widget.scrollController.removeListener(_onScroll);
    super.dispose();
  }

  void _onScroll() {
    if (!widget.scrollController.hasClients) return;
    final position = widget.scrollController.position;
    final scrolledUp = position.maxScrollExtent - position.pixels > 240;
    if (scrolledUp != _showJumpButton) {
      setState(() => _showJumpButton = scrolledUp);
    }
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();
    final messages = provider.messages;
    final isThinking = provider.isThinking;

    if (messages.isEmpty && !isThinking) {
      return _EmptyState(onSuggestion: widget.onSuggestion);
    }

    return Stack(
      children: [
        // Lets a selection span several bubbles instead of stopping at each.
        SelectionArea(
          child: ListView.builder(
            controller: widget.scrollController,
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
            itemCount: messages.length + (isThinking ? 1 : 0),
            itemBuilder: (context, index) {
              if (index == messages.length) {
                return Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: ThinkingIndicator(label: provider.activeTool),
                );
              }
              final isLast = index == messages.length - 1;
              return Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: MessageBubble(
                  message: messages[index],
                  isStreaming: provider.isStreaming &&
                      isLast &&
                      messages[index].role == MessageRole.assistant,
                ),
              );
            },
          ),
        ),
        if (_showJumpButton)
          Positioned(
            bottom: 12,
            left: 0,
            right: 0,
            child: Center(
              child: _JumpToLatest(
                onTap: () => widget.scrollController.animateTo(
                  widget.scrollController.position.maxScrollExtent,
                  duration: const Duration(milliseconds: 260),
                  curve: Curves.easeOut,
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _JumpToLatest extends StatelessWidget {
  const _JumpToLatest({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: kSurfaceVar,
      borderRadius: BorderRadius.circular(20),
      elevation: 4,
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: kBorder),
          ),
          child: const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.arrow_downward_rounded, size: 14, color: kPrimaryLight),
              SizedBox(width: 6),
              Text('Latest',
                  style: TextStyle(color: kTextSecondary, fontSize: 12)),
            ],
          ),
        ),
      ),
    ).animate().fadeIn(duration: 160.ms).slideY(begin: 0.3, duration: 160.ms);
  }
}

/// Shown on an empty conversation. The chips double as documentation for what
/// the assistant can actually do.
class _EmptyState extends StatelessWidget {
  const _EmptyState({this.onSuggestion});
  final void Function(String)? onSuggestion;

  static const _suggestions = <(IconData, String)>[
    (Icons.fitness_center_rounded, 'What should I do at the gym today?'),
    (Icons.restaurant_rounded, 'How am I doing on calories?'),
    (Icons.checklist_rounded, "What's on my task list?"),
    (Icons.insights_rounded, 'How was my week?'),
  ];

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Ask me anything',
              style: TextStyle(
                color: kTextPrimary,
                fontSize: 17,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            const Text(
              'Workouts, meals, tasks — speak or type.',
              textAlign: TextAlign.center,
              style: TextStyle(color: kTextMuted, fontSize: 13, height: 1.5),
            ),
            const SizedBox(height: 20),
            Wrap(
              alignment: WrapAlignment.center,
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final (icon, text) in _suggestions)
                  _SuggestionChip(
                    icon: icon,
                    label: text,
                    onTap: () => onSuggestion?.call(text),
                  ),
              ],
            ),
          ],
        ),
      ).animate().fadeIn(duration: 400.ms),
    );
  }
}

class _SuggestionChip extends StatelessWidget {
  const _SuggestionChip({
    required this.icon,
    required this.label,
    required this.onTap,
  });
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: kSurfaceVar,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: kBorder),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 14, color: kPrimaryLight),
              const SizedBox(width: 7),
              Flexible(
                child: Text(
                  label,
                  style: const TextStyle(color: kTextSecondary, fontSize: 12.5),
                ),
              ),
            ],
          ),
        ),
      ),
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
    final busy = provider.isThinking || provider.isTranscribing;
    final isDesktop = MediaQuery.of(context).size.width >= 800;

    // 44pt is the minimum comfortable touch target; desktop can be tighter.
    final buttonSize = isDesktop ? 40.0 : 46.0;

    return Container(
      padding: EdgeInsets.fromLTRB(16, 10, 16, isDesktop ? 14 : 10),
      decoration: const BoxDecoration(
        color: kSurface,
        border: Border(top: BorderSide(color: kBorder)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              enabled: !busy,
              maxLines: 5,
              minLines: 1,
              textInputAction:
                  isDesktop ? TextInputAction.send : TextInputAction.newline,
              keyboardType: TextInputType.multiline,
              textCapitalization: TextCapitalization.sentences,
              // 13pt is uncomfortably small on a phone.
              style: TextStyle(
                color: kTextPrimary,
                fontSize: isDesktop ? 13 : 15,
              ),
              decoration: InputDecoration(
                isDense: true,
                hintText: provider.isTranscribing
                    ? 'Transcribing…'
                    : provider.isThinking
                        ? 'Thinking…'
                        : 'Ask PAI anything',
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: isDesktop ? 12 : 14,
                ),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: kBorder),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: kBorder),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: kAccent, width: 1.5),
                ),
              ),
              onSubmitted: (_) => onSend(),
            ),
          ),
          const SizedBox(width: 8),
          // Listens to the controller so the button reflects whether there is
          // anything to send, instead of looking tappable when it is inert.
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: controller,
            builder: (context, value, _) {
              final canSend = !busy && value.text.trim().isNotEmpty;
              return Semantics(
                button: true,
                enabled: canSend,
                label: 'Send message',
                child: AnimatedOpacity(
                  opacity: canSend ? 1 : 0.4,
                  duration: const Duration(milliseconds: 150),
                  child: Material(
                    borderRadius: BorderRadius.circular(12),
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: canSend ? onSend : null,
                      child: Container(
                        width: buttonSize,
                        height: buttonSize,
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(12),
                          gradient: canSend
                              ? const LinearGradient(
                                  colors: [kPrimary, Color(0xFF5B21B6)])
                              : null,
                          color: canSend ? null : kSurfaceVar,
                          border: canSend
                              ? null
                              : Border.all(color: kBorder),
                        ),
                        child: busy
                            ? const Padding(
                                padding: EdgeInsets.all(12),
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: kTextMuted,
                                ),
                              )
                            : Icon(
                                Icons.arrow_upward_rounded,
                                color: canSend ? Colors.white : kTextMuted,
                                size: 19,
                              ),
                      ),
                    ),
                  ),
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}
