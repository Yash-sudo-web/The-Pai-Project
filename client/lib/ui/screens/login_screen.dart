import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/auth_provider.dart';

/// Password sign-in. On first run it also asks for the backend URL.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _passwordController = TextEditingController();
  final _urlController = TextEditingController();
  final _passwordFocus = FocusNode();
  bool _obscure = true;
  String? _localError;

  @override
  void initState() {
    super.initState();
    _urlController.text = context.read<AuthProvider>().baseUrl;
  }

  @override
  void dispose() {
    _passwordController.dispose();
    _urlController.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final auth = context.read<AuthProvider>();
    if (auth.busy) return;

    final url = _urlController.text.trim();
    if (url.isEmpty || !url.startsWith('http')) {
      setState(() => _localError = 'Enter a valid backend URL (starts with https://)');
      return;
    }
    setState(() => _localError = null);
    await auth.signIn(_passwordController.text, baseUrl: url);
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthProvider>();
    final needsUrl = auth.state == AuthState.needsSetup || auth.baseUrl.isEmpty;

    return Scaffold(
      backgroundColor: kBg,
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 400),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _logo(),
                const SizedBox(height: 22),
                const Text(
                  'Welcome back',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: kTextPrimary,
                    fontSize: 24,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  auth.passwordLoginAvailable
                      ? 'Sign in once — you\'ll stay signed in for ${auth.sessionDays} days.'
                      : 'Password login is not enabled on this server.',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: kTextMuted, fontSize: 13, height: 1.5),
                ),
                const SizedBox(height: 28),

                if (needsUrl) ...[
                  const _Label('Backend URL'),
                  const SizedBox(height: 6),
                  TextField(
                    controller: _urlController,
                    keyboardType: TextInputType.url,
                    autocorrect: false,
                    style: const TextStyle(color: kTextPrimary, fontSize: 14),
                    decoration: const InputDecoration(
                      hintText: 'https://your-app.vercel.app',
                      prefixIcon: Icon(Icons.link_rounded, color: kTextMuted, size: 18),
                    ),
                    onSubmitted: (_) => _passwordFocus.requestFocus(),
                  ),
                  const SizedBox(height: 16),
                ],

                const _Label('Password'),
                const SizedBox(height: 6),
                TextField(
                  controller: _passwordController,
                  focusNode: _passwordFocus,
                  obscureText: _obscure,
                  autofocus: !needsUrl,
                  enabled: !auth.busy,
                  // Lets the platform password manager save and refill it.
                  autofillHints: const [AutofillHints.password],
                  style: const TextStyle(color: kTextPrimary, fontSize: 14),
                  decoration: InputDecoration(
                    hintText: 'Your password',
                    prefixIcon: const Icon(Icons.lock_rounded, color: kTextMuted, size: 18),
                    suffixIcon: IconButton(
                      icon: Icon(
                        _obscure ? Icons.visibility_rounded : Icons.visibility_off_rounded,
                        color: kTextMuted,
                        size: 18,
                      ),
                      onPressed: () => setState(() => _obscure = !_obscure),
                    ),
                  ),
                  onSubmitted: (_) => _submit(),
                ),

                if ((_localError ?? auth.error) != null) ...[
                  const SizedBox(height: 12),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.error_outline_rounded, color: kError, size: 16),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          _localError ?? auth.error!,
                          style: const TextStyle(color: kError, fontSize: 12, height: 1.4),
                        ),
                      ),
                    ],
                  ),
                ],

                const SizedBox(height: 24),
                SizedBox(
                  height: 48,
                  child: ElevatedButton(
                    onPressed: auth.busy ? null : _submit,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: kPrimary,
                      foregroundColor: Colors.white,
                      disabledBackgroundColor: kSurfaceVar,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                    child: auth.busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white),
                          )
                        : const Text('Sign in',
                            style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                  ),
                ),

                if (!auth.passwordLoginAvailable) ...[
                  const SizedBox(height: 18),
                  Text(
                    'Set PAI_PASSWORD and PAI_JWT_SECRET in your .env\n'
                    '(or your host\'s environment variables), then restart.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: kTextMuted.withOpacity(0.8),
                      fontSize: 11,
                      height: 1.6,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _logo() {
    return Center(
      child: Container(
        width: 56,
        height: 56,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: const LinearGradient(
            colors: [kPrimary, Color(0xFF4C1D95)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: const Icon(Icons.auto_awesome_rounded, color: Colors.white, size: 26),
      ).animate().fadeIn(duration: 400.ms).scaleXY(begin: 0.85, curve: Curves.easeOutBack),
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
