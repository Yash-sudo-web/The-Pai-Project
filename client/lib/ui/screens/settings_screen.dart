import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/auth_provider.dart';
import '../../providers/chat_provider.dart';

class SettingsDialog extends StatefulWidget {
  const SettingsDialog({super.key});

  @override
  State<SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<SettingsDialog> {
  late final TextEditingController _urlController;
  late final TextEditingController _keyController;
  late final TextEditingController _groqKeyController;
  bool _obscureKey = true;
  bool _obscureGroqKey = true;
  bool _saving = false;
  String? _saveError;

  @override
  void initState() {
    super.initState();
    final provider = context.read<ChatProvider>();
    _urlController = TextEditingController(text: provider.currentBaseUrl);
    _keyController = TextEditingController(text: provider.currentApiKey);
    _groqKeyController = TextEditingController(text: provider.currentGroqKey);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _keyController.dispose();
    _groqKeyController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final url = _urlController.text.trim();
    final key = _keyController.text.trim();
    final groqKey = _groqKeyController.text.trim();

    if (url.isEmpty || !url.startsWith('http')) {
      setState(() => _saveError = 'Enter a valid URL (starts with https://)');
      return;
    }

    setState(() {
      _saving = true;
      _saveError = null;
    });

    await context.read<ChatProvider>().saveConfig(
      baseUrl: url,
      apiKey: key,
      groqApiKey: groqKey,
    );

    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: kSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: kBorder),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480),
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: kPrimary.withOpacity(0.15),
                    ),
                    child: const Icon(Icons.settings_rounded,
                        color: kPrimaryLight, size: 20),
                  ),
                  const SizedBox(width: 12),
                  const Text(
                    'Settings',
                    style: TextStyle(
                      color: kTextPrimary,
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close_rounded,
                        color: kTextSecondary, size: 20),
                    onPressed: () {
                      // only allow close if already configured
                      final p = context.read<ChatProvider>();
                      if (p.isConfigured) Navigator.of(context).pop();
                    },
                  ),
                ],
              ),
              const SizedBox(height: 24),

              // API URL
              const _Label('Backend URL'),
              const SizedBox(height: 6),
              TextField(
                controller: _urlController,
                style:
                    const TextStyle(color: kTextPrimary, fontSize: 14),
                decoration: const InputDecoration(
                  hintText: 'https://your-app.vercel.app',
                  prefixIcon: Icon(Icons.link_rounded,
                      color: kTextMuted, size: 18),
                ),
              ),
              const SizedBox(height: 16),

              // Session
              const _Label('Session'),
              const SizedBox(height: 6),
              const _SessionRow(),
              const SizedBox(height: 16),

              // API Key — only needed for scripts/curl now that the app
              // signs in with a password.
              const _Label('API Key (optional)'),
              const SizedBox(height: 6),
              TextField(
                controller: _keyController,
                obscureText: _obscureKey,
                style:
                    const TextStyle(color: kTextPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'only for scripts — leave blank',
                  prefixIcon: const Icon(Icons.key_rounded,
                      color: kTextMuted, size: 18),
                  suffixIcon: IconButton(
                    icon: Icon(
                      _obscureKey
                          ? Icons.visibility_rounded
                          : Icons.visibility_off_rounded,
                      color: kTextMuted,
                      size: 18,
                    ),
                    onPressed: () =>
                        setState(() => _obscureKey = !_obscureKey),
                  ),
                ),
              ),
              const SizedBox(height: 16),

              // Groq API Key (for voice)
              const _Label('Groq API Key (voice)'),
              const SizedBox(height: 6),
              TextField(
                controller: _groqKeyController,
                obscureText: _obscureGroqKey,
                style:
                    const TextStyle(color: kTextPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'gsk_... (optional — enables voice)',
                  prefixIcon: const Icon(Icons.mic_rounded,
                      color: kTextMuted, size: 18),
                  suffixIcon: IconButton(
                    icon: Icon(
                      _obscureGroqKey
                          ? Icons.visibility_rounded
                          : Icons.visibility_off_rounded,
                      color: kTextMuted,
                      size: 18,
                    ),
                    onPressed: () =>
                        setState(() => _obscureGroqKey = !_obscureGroqKey),
                  ),
                ),
              ),

              if (_saveError != null) ...[
                const SizedBox(height: 10),
                Text(_saveError!,
                    style:
                        const TextStyle(color: kError, fontSize: 12)),
              ],

              const SizedBox(height: 24),

              // Save button
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _save,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: kPrimary,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10)),
                  ),
                  child: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: Colors.white),
                        )
                      : const Text('Save & Connect',
                          style: TextStyle(
                              fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Shows how long the current session lasts, with a sign-out button.
class _SessionRow extends StatelessWidget {
  const _SessionRow();

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    final expiry = auth.sessionExpiry;
    final signedInWithPassword = expiry != null;

    final label = signedInWithPassword
        ? 'Signed in — expires ${_relative(expiry)}'
        : 'Using an API key (no session)';

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: kSurfaceVar,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        children: [
          Icon(
            signedInWithPassword
                ? Icons.verified_user_rounded
                : Icons.key_rounded,
            color: signedInWithPassword ? kSuccess : kTextMuted,
            size: 16,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              label,
              style: const TextStyle(color: kTextSecondary, fontSize: 12),
            ),
          ),
          // Always offer a route to the login screen. Without this, anyone
          // running on a stored API key has no way to reach it.
          TextButton(
            onPressed: () async {
              Navigator.of(context).pop();
              await context.read<AuthProvider>().signOut();
            },
            style: TextButton.styleFrom(
              foregroundColor: signedInWithPassword ? kError : kPrimaryLight,
              padding: const EdgeInsets.symmetric(horizontal: 10),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              signedInWithPassword ? 'Sign out' : 'Sign in',
              style: const TextStyle(fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  static String _relative(DateTime when) {
    final days = when.difference(DateTime.now()).inDays;
    if (days < 0) return 'now';
    if (days == 0) return 'today';
    if (days == 1) return 'tomorrow';
    return 'in $days days';
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        color: kTextSecondary,
        fontSize: 12,
        fontWeight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
    );
  }
}
