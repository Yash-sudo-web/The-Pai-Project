import 'package:shared_preferences/shared_preferences.dart';

/// Centralised configuration — reads from SharedPreferences first,
/// then falls back to --dart-define compile-time env vars.
class AppConfig {
  AppConfig._();

  static const _keyBaseUrl = 'pai_base_url';
  static const _keyApiKey = 'pai_api_key';

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

  static Future<void> saveBaseUrl(SharedPreferences prefs, String url) =>
      prefs.setString(_keyBaseUrl, url.trim());

  static Future<void> saveApiKey(SharedPreferences prefs, String key) =>
      prefs.setString(_keyApiKey, key.trim());

  static bool isConfigured(SharedPreferences prefs) {
    final url = getBaseUrl(prefs);
    final key = getApiKey(prefs);
    return url.isNotEmpty && key.isNotEmpty;
  }
}
