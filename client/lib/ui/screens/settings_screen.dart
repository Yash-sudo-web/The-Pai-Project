import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/chat_provider.dart';

class SettingsDialog extends StatefulWidget {
  const SettingsDialog({super.key});

  @override
  State<SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<SettingsDialog> {
  late final TextEditingController _urlController;
  late final TextEditingController _keyController;
  bool _obscureKey = true;
  bool _saving = false;
  String? _saveError;

  @override
  void initState() {
    super.initState();
    final provider = context.read<ChatProvider>();
    _urlController = TextEditingController(text: provider.currentBaseUrl);
    _keyController = TextEditingController(text: provider.currentApiKey);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _keyController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final url = _urlController.text.trim();
    final key = _keyController.text.trim();

    if (url.isEmpty || !url.startsWith('http')) {
      setState(() => _saveError = 'Enter a valid URL (starts with https://)');
      return;
    }
    if (key.isEmpty) {
      setState(() => _saveError = 'API key cannot be empty');
      return;
    }

    setState(() {
      _saving = true;
      _saveError = null;
    });

    await context.read<ChatProvider>().saveConfig(baseUrl: url, apiKey: key);

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

              // API Key
              const _Label('API Key'),
              const SizedBox(height: 6),
              TextField(
                controller: _keyController,
                obscureText: _obscureKey,
                style:
                    const TextStyle(color: kTextPrimary, fontSize: 14),
                decoration: InputDecoration(
                  hintText: 'your_api_key',
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
