import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../providers/auth_provider.dart';
import 'chat_screen.dart';
import 'login_screen.dart';

/// Chooses between the login screen and the app based on session state.
class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  @override
  void initState() {
    super.initState();
    // Deferred so the first frame renders the splash immediately.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<AuthProvider>().bootstrap();
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AuthProvider>().state;

    return switch (state) {
      AuthState.checking => const _Splash(),
      AuthState.needsSetup || AuthState.signedOut => const LoginScreen(),
      AuthState.signedIn => const ChatScreen(),
    };
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: kBg,
      body: Center(
        child: SizedBox(
          width: 26,
          height: 26,
          child: CircularProgressIndicator(strokeWidth: 2, color: kPrimaryLight),
        ),
      ),
    );
  }
}
