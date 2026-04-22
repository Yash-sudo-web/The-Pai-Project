import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/chat_provider.dart';

class AppSidebar extends StatelessWidget {
  const AppSidebar({super.key, required this.onSettingsTap});
  final VoidCallback onSettingsTap;

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ChatProvider>();

    return Container(
      width: 260,
      color: kSurface,
      child: Column(
        children: [
          // ── Logo ───────────────────────────────────────────────────────
          const SizedBox(height: 24),
          _Logo(),
          const SizedBox(height: 32),

          // ── Nav items ─────────────────────────────────────────────────
          _NavItem(
            icon: Icons.chat_bubble_outline_rounded,
            label: 'Chat',
            selected: true,
            onTap: () {},
          ),
          _NavItem(
            icon: Icons.history_rounded,
            label: 'History',
            selected: false,
            onTap: () async => context.read<ChatProvider>().loadHistory(),
          ),
          _NavItem(
            icon: Icons.settings_outlined,
            label: 'Settings',
            selected: false,
            onTap: onSettingsTap,
          ),

          const Spacer(),

          // ── TTS toggle ────────────────────────────────────────────────
          _TtsToggle(
            enabled: provider.ttsEnabled,
            onChanged: provider.setTtsEnabled,
          ),
          const SizedBox(height: 8),

          const Divider(color: kBorder, height: 1),

          // ── Status ────────────────────────────────────────────────────
          _StatusFooter(isOnline: provider.isOnline),
          const SizedBox(height: 16),
        ],
      ),
    );
  }
}

class _Logo extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: const LinearGradient(
              colors: [kPrimary, Color(0xFF4C1D95)],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
            boxShadow: [
              BoxShadow(color: kPrimary.withOpacity(0.4), blurRadius: 12),
            ],
          ),
          child: const Icon(Icons.auto_awesome_rounded,
              color: Colors.white, size: 18),
        ),
        const SizedBox(width: 10),
        const Text(
          'PAI',
          style: TextStyle(
            color: kTextPrimary,
            fontSize: 20,
            fontWeight: FontWeight.w800,
            letterSpacing: 1.5,
          ),
        ),
      ],
    ).animate().fadeIn(duration: 400.ms);
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(10),
              color: selected ? kPrimary.withOpacity(0.15) : Colors.transparent,
            ),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected ? kPrimaryLight : kTextSecondary,
                ),
                const SizedBox(width: 12),
                Text(
                  label,
                  style: TextStyle(
                    color: selected ? kPrimaryLight : kTextSecondary,
                    fontSize: 14,
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

class _TtsToggle extends StatelessWidget {
  const _TtsToggle({required this.enabled, required this.onChanged});
  final bool enabled;
  final void Function(bool) onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: [
          Icon(
            enabled ? Icons.volume_up_rounded : Icons.volume_off_rounded,
            size: 18,
            color: enabled ? kPrimaryLight : kTextMuted,
          ),
          const SizedBox(width: 10),
          Text(
            'Voice responses',
            style: TextStyle(
              color: enabled ? kTextSecondary : kTextMuted,
              fontSize: 13,
            ),
          ),
          const Spacer(),
          Switch(
            value: enabled,
            onChanged: onChanged,
            activeColor: kPrimary,
            activeTrackColor: kPrimary.withOpacity(0.3),
            inactiveThumbColor: kTextMuted,
            inactiveTrackColor: kSurfaceVar,
          ),
        ],
      ),
    );
  }
}

class _StatusFooter extends StatelessWidget {
  const _StatusFooter({required this.isOnline});
  final bool isOnline;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isOnline ? kSuccess : kError,
            ),
          ),
          const SizedBox(width: 8),
          Text(
            isOnline ? 'Connected' : 'Offline',
            style: TextStyle(
              color: isOnline ? kSuccess : kError,
              fontSize: 12,
            ),
          ),
        ],
      ),
    );
  }
}
