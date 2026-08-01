import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:pai_client/config.dart';
import 'package:pai_client/providers/auth_provider.dart';
import 'package:pai_client/services/api_service.dart';
import 'package:pai_client/ui/screens/login_screen.dart';

Future<AuthProvider> buildAuth(Map<String, Object> prefsValues) async {
  SharedPreferences.setMockInitialValues(prefsValues);
  final prefs = await SharedPreferences.getInstance();
  return AuthProvider(api: ApiService(prefs: prefs), prefs: prefs);
}

Future<void> pumpLogin(WidgetTester tester, AuthProvider auth) async {
  await tester.pumpWidget(
    ChangeNotifierProvider<AuthProvider>.value(
      value: auth,
      child: const MaterialApp(home: LoginScreen()),
    ),
  );
  // The logo runs a one-shot entrance animation; settle it so no timer is
  // still pending when the test tears down.
  await tester.pumpAndSettle();
}

void main() {
  group('LoginScreen', () {
    testWidgets('renders a password field and a sign-in button',
        (tester) async {
      final auth = await buildAuth({'pai_base_url': 'http://localhost:8000'});
      await pumpLogin(tester, auth);

      expect(find.text('Welcome back'), findsOneWidget);
      expect(find.text('Sign in'), findsOneWidget);
      expect(find.byType(TextField), findsNWidgets(2));
      expect(tester.takeException(), isNull);
    });

    testWidgets('backend URL stays editable once one is saved', (tester) async {
      // Regression: the field used to appear only on first run, which left a
      // misconfigured backend unreachable from the UI.
      final auth = await buildAuth({'pai_base_url': 'https://example.com'});
      await pumpLogin(tester, auth);

      expect(find.text('Backend URL'), findsOneWidget);
      final urlField =
          tester.widgetList<TextField>(find.byType(TextField)).first;
      expect(urlField.controller?.text, 'https://example.com');
    });

    testWidgets('rejects an empty backend URL before calling the network',
        (tester) async {
      final auth = await buildAuth({});
      await pumpLogin(tester, auth);

      await tester.tap(find.text('Sign in'));
      await tester.pumpAndSettle();

      expect(find.textContaining('valid backend URL'), findsOneWidget);
    });
  });

  group('AppConfig session handling', () {
    test('a stored API key alone is not a session', () async {
      SharedPreferences.setMockInitialValues({
        'pai_base_url': 'http://localhost:8000',
        'pai_api_key': 'a-long-enough-api-key-value-here',
      });
      final prefs = await SharedPreferences.getInstance();

      expect(AppConfig.hasValidToken(prefs), isFalse);
      // Still a usable credential for requests, just not a login session.
      expect(AppConfig.getCredential(prefs), isNotEmpty);
    });

    test('an expired token is not valid', () async {
      SharedPreferences.setMockInitialValues({
        'pai_auth_token': 'stale.jwt.value',
        'pai_auth_token_expires_at':
            DateTime.now().subtract(const Duration(days: 1)).toIso8601String(),
      });
      final prefs = await SharedPreferences.getInstance();

      expect(AppConfig.hasValidToken(prefs), isFalse);
    });

    test('a fresh token is valid and not due for refresh', () async {
      SharedPreferences.setMockInitialValues({
        'pai_auth_token': 'fresh.jwt.value',
        'pai_auth_token_expires_at':
            DateTime.now().add(const Duration(days: 30)).toIso8601String(),
      });
      final prefs = await SharedPreferences.getInstance();

      expect(AppConfig.hasValidToken(prefs), isTrue);
      expect(AppConfig.shouldRefreshToken(prefs), isFalse);
    });

    test('a token inside the renewal window is due for refresh', () async {
      SharedPreferences.setMockInitialValues({
        'pai_auth_token': 'ageing.jwt.value',
        'pai_auth_token_expires_at':
            DateTime.now().add(const Duration(days: 3)).toIso8601String(),
      });
      final prefs = await SharedPreferences.getInstance();

      expect(AppConfig.hasValidToken(prefs), isTrue);
      expect(AppConfig.shouldRefreshToken(prefs), isTrue);
    });

    test('the session token wins over an API key', () async {
      SharedPreferences.setMockInitialValues({
        'pai_auth_token': 'session-token',
        'pai_api_key': 'api-key',
      });
      final prefs = await SharedPreferences.getInstance();

      expect(AppConfig.getCredential(prefs), 'session-token');
    });
  });
}
