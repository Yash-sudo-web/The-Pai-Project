import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'ui/screens/chat_screen.dart';

// ── Design tokens ──────────────────────────────────────────────────────────
const kBg           = Color(0xFF08080F);
const kSurface      = Color(0xFF11111C);
const kSurfaceVar   = Color(0xFF191926);
const kBorder       = Color(0xFF2A2A3D);
const kPrimary      = Color(0xFF7C3AED);
const kPrimaryLight = Color(0xFFA78BFA);
const kAccent       = Color(0xFF06B6D4);
const kSuccess      = Color(0xFF34D399);
const kError        = Color(0xFFF87171);
const kTextPrimary  = Color(0xFFF1F5F9);
const kTextSecondary= Color(0xFF94A3B8);
const kTextMuted    = Color(0xFF64748B);

class PaiApp extends StatelessWidget {
  const PaiApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'PAI — Personal AI Assistant',
      debugShowCheckedModeBanner: false,
      theme: _buildTheme(),
      home: const ChatScreen(),
    );
  }

  ThemeData _buildTheme() {
    final base = GoogleFonts.interTextTheme();
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: kBg,
      colorScheme: const ColorScheme.dark(
        background: kBg,
        surface: kSurface,
        surfaceVariant: kSurfaceVar,
        primary: kPrimary,
        onPrimary: Colors.white,
        secondary: kAccent,
        onSecondary: Colors.white,
        onBackground: kTextPrimary,
        onSurface: kTextPrimary,
        onSurfaceVariant: kTextSecondary,
        outline: kBorder,
        error: kError,
        onError: Colors.white,
      ),
      textTheme: base.copyWith(
        bodyLarge:   base.bodyLarge?.copyWith(color: kTextPrimary, fontSize: 15),
        bodyMedium:  base.bodyMedium?.copyWith(color: kTextPrimary, fontSize: 14),
        bodySmall:   base.bodySmall?.copyWith(color: kTextSecondary, fontSize: 12),
        labelSmall:  base.labelSmall?.copyWith(color: kTextMuted, fontSize: 11),
        titleMedium: base.titleMedium?.copyWith(color: kTextPrimary, fontWeight: FontWeight.w600),
        titleLarge:  base.titleLarge?.copyWith(color: kTextPrimary, fontWeight: FontWeight.w700),
      ),
      dividerColor: kBorder,
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: kSurfaceVar,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
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
          borderSide: const BorderSide(color: kPrimaryLight, width: 1.5),
        ),
        hintStyle: const TextStyle(color: kTextMuted),
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: MaterialStateProperty.all(kBorder),
        radius: const Radius.circular(4),
      ),
    );
  }
}
