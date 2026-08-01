import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../services/api_service.dart';

enum AuthState {
  /// Deciding on startup whether the stored session is still good.
  checking,

  /// No backend URL yet — first run.
  needsSetup,

  /// Backend known, but no valid session.
  signedOut,

  signedIn,
}

/// Owns the session token so the password is entered once, not every launch.
class AuthProvider extends ChangeNotifier {
  AuthProvider({required ApiService api, required SharedPreferences prefs})
      : _api = api,
        _prefs = prefs;

  final ApiService _api;
  final SharedPreferences _prefs;

  AuthState _state = AuthState.checking;
  String? _error;
  bool _busy = false;
  bool _passwordLoginAvailable = true;
  int _sessionDays = 30;

  AuthState get state => _state;
  String? get error => _error;
  bool get busy => _busy;
  bool get passwordLoginAvailable => _passwordLoginAvailable;
  int get sessionDays => _sessionDays;
  String get baseUrl => AppConfig.getBaseUrl(_prefs);
  DateTime? get sessionExpiry => AppConfig.getTokenExpiry(_prefs);

  /// Resolve the startup state, renewing the session when it is close to
  /// expiring so day-to-day use never hits a login prompt.
  Future<void> bootstrap() async {
    if (!AppConfig.hasBaseUrl(_prefs)) {
      _set(AuthState.needsSetup);
      return;
    }

    // Awaited, not fire-and-forget: the decision below depends on whether the
    // server offers password login.
    await _loadAuthStatus();

    if (AppConfig.getToken(_prefs).isEmpty) {
      // A hand-entered API key is a credential for scripts, not a session.
      // Falling back to it here would skip the login screen entirely for
      // anyone who had configured a key before password login existed.
      final legacyKeyOnly =
          !_passwordLoginAvailable && AppConfig.getApiKey(_prefs).isNotEmpty;
      _set(legacyKeyOnly ? AuthState.signedIn : AuthState.signedOut);
      return;
    }

    if (!AppConfig.hasValidToken(_prefs)) {
      await AppConfig.clearToken(_prefs);
      _set(AuthState.signedOut);
      return;
    }

    // Token looks valid locally; confirm with the server, but stay signed in
    // if the server is merely unreachable.
    final ok = await _api.verifyCredential();
    if (!ok) {
      await AppConfig.clearToken(_prefs);
      _set(AuthState.signedOut);
      return;
    }

    _set(AuthState.signedIn);
    if (AppConfig.shouldRefreshToken(_prefs)) unawaited(refreshSession());
  }

  Future<void> _loadAuthStatus({String? baseUrl}) async {
    try {
      final status = await _api.authStatus(baseUrl: baseUrl);
      _passwordLoginAvailable = status.passwordLogin;
      _sessionDays = status.sessionDays;
      notifyListeners();
    } catch (_) {
      // Older backend or offline — leave the login form enabled.
    }
  }

  /// Save the backend URL during first-run setup.
  Future<void> setBaseUrl(String url) async {
    await AppConfig.saveBaseUrl(_prefs, url);
    _error = null;
    await _loadAuthStatus(baseUrl: url);
    _set(AuthState.signedOut);
  }

  Future<bool> signIn(String password, {String? baseUrl}) async {
    if (password.isEmpty) {
      _error = 'Enter your password.';
      notifyListeners();
      return false;
    }

    _busy = true;
    _error = null;
    notifyListeners();

    try {
      if (baseUrl != null && baseUrl.isNotEmpty) {
        await AppConfig.saveBaseUrl(_prefs, baseUrl);
      }
      final result = await _api.login(password);
      await AppConfig.saveToken(_prefs, result.token, result.expiresAt);
      _busy = false;
      _set(AuthState.signedIn);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
    } catch (e) {
      _error = 'Sign-in failed: $e';
    }

    _busy = false;
    notifyListeners();
    return false;
  }

  Future<void> refreshSession() async {
    final result = await _api.refreshSession();
    if (result == null) return;
    await AppConfig.saveToken(_prefs, result.token, result.expiresAt);
    notifyListeners();
  }

  Future<void> signOut() async {
    await AppConfig.clearToken(_prefs);
    _error = null;
    _set(AuthState.signedOut);
  }

  /// Called when a request comes back 401 — the session died server-side.
  Future<void> onUnauthorized() async {
    if (_state != AuthState.signedIn) return;
    await AppConfig.clearToken(_prefs);
    _error = 'Your session expired. Please sign in again.';
    _set(AuthState.signedOut);
  }

  void _set(AuthState next) {
    _state = next;
    notifyListeners();
  }
}
