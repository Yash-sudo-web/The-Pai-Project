import 'package:shared_preferences/shared_preferences.dart';

/// Centralised configuration — reads from SharedPreferences first,
/// then falls back to --dart-define compile-time env vars.
class AppConfig {
  AppConfig._();

  static const _keyBaseUrl = 'pai_base_url';
  static const _keyApiKey = 'pai_api_key';
  static const _keyGroqKey = 'pai_groq_api_key';
  static const _keyToken = 'pai_auth_token';
  static const _keyTokenExpiry = 'pai_auth_token_expires_at';

  static String getBaseUrl(SharedPreferences prefs) {
    var url = prefs.getString(_keyBaseUrl) ??
        const String.fromEnvironment('PAI_BASE_URL', defaultValue: '');
    if (url.endsWith('/')) url = url.substring(0, url.length - 1);
    return url;
  }

  static String getApiKey(SharedPreferences prefs) {
    return prefs.getString(_keyApiKey) ??
        const String.fromEnvironment('PAI_API_KEY', defaultValue: '');
  }

  static String getGroqApiKey(SharedPreferences prefs) {
    return prefs.getString(_keyGroqKey) ??
        const String.fromEnvironment('GROQ_API_KEY', defaultValue: '');
  }

  static Future<void> saveBaseUrl(SharedPreferences prefs, String url) =>
      prefs.setString(_keyBaseUrl, url.trim());

  static Future<void> saveApiKey(SharedPreferences prefs, String key) =>
      prefs.setString(_keyApiKey, key.trim());

  static Future<void> saveGroqApiKey(SharedPreferences prefs, String key) =>
      prefs.setString(_keyGroqKey, key.trim());

  // ── Session token (password login) ────────────────────────────────────────

  /// The stored session token, or '' when there is none.
  static String getToken(SharedPreferences prefs) =>
      prefs.getString(_keyToken) ?? '';

  static DateTime? getTokenExpiry(SharedPreferences prefs) {
    final raw = prefs.getString(_keyTokenExpiry);
    return raw == null ? null : DateTime.tryParse(raw);
  }

  static Future<void> saveToken(
    SharedPreferences prefs,
    String token,
    DateTime? expiresAt,
  ) async {
    await prefs.setString(_keyToken, token);
    if (expiresAt != null) {
      await prefs.setString(_keyTokenExpiry, expiresAt.toIso8601String());
    } else {
      await prefs.remove(_keyTokenExpiry);
    }
  }

  static Future<void> clearToken(SharedPreferences prefs) async {
    await prefs.remove(_keyToken);
    await prefs.remove(_keyTokenExpiry);
  }

  /// True when a token exists and has not passed its expiry.
  static bool hasValidToken(SharedPreferences prefs) {
    if (getToken(prefs).isEmpty) return false;
    final expiry = getTokenExpiry(prefs);
    return expiry == null || expiry.isAfter(DateTime.now());
  }

  /// Renew when less than a quarter of the session remains, so ordinary use
  /// never runs into an expiry.
  static bool shouldRefreshToken(SharedPreferences prefs) {
    final expiry = getTokenExpiry(prefs);
    if (expiry == null) return false;
    return DateTime.now().isAfter(expiry.subtract(const Duration(days: 7)));
  }

  /// The credential to send: a session token when present, else the API key.
  static String getCredential(SharedPreferences prefs) {
    final token = getToken(prefs);
    return token.isNotEmpty ? token : getApiKey(prefs);
  }

  /// Whether the backend URL is known — everything else is resolved by login.
  static bool hasBaseUrl(SharedPreferences prefs) =>
      getBaseUrl(prefs).isNotEmpty;

  static bool isConfigured(SharedPreferences prefs) {
    return hasBaseUrl(prefs) && getCredential(prefs).isNotEmpty;
  }
}

