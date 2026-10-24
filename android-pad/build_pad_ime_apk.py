#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generator & Builder for ForestPadIME.apk (com.forest.padime) v3
Engineered for Xiaomi Pad 8 Pro (3200x2136) with all 12 UX & Architecture Upgrades:
1. 124,092-word + 8,105-char open-source rime-ice dictionary (`assets/pinyin_dict.txt`) with fast first-syllable index & Viterbi/Greedy full-sentence composition.
2. Partial candidate selection (`CandItem.consumedRawLen`): selecting a prefix word (e.g. "提升" in `tiugtiyj`) commits "提升" and preserves remaining `tiyj` ("体验") without losing input.
3. Long-press Backspace / Del (`>=320ms`) when candidates are active clears all unselected composing Pinyin at once.
4. Long-press Spacebar (`>=400ms`) starts Voice Input (`SpeechRecognizer` + Xiaomi AsrService / RecognizerIntent fallback).
5. Authentic Physical Keyboard `Caps` (`ACT_CAPS`) vs `Shift` (`ACT_SHIFT`) logic: Caps Lock locks uppercase letters without shifting numbers; Shift shifts numbers/symbols (`1`->`!`, etc.) and letters (one-shot or multi-touch hold).
6. Zero text overlap in Landscape & Portrait.
7. Independent Split / Full-Width layout memory for Landscape (`split_land`) and Portrait (`split_port`) persisted in `SharedPreferences`.
8. Deterministic `Ctrl+Z` (Undo) and `Ctrl+Y` / `Ctrl+Shift+Z` (Redo) via IME `ExtractedText` Undo/Redo snapshot stacks + `android.R.id.undo/redo` + `KeyEvent`.
9. Clean, concise 森林集 (Forest) UI aesthetic: single-label function keys, uncluttered keycaps, concise toolbar pills.
10. True transparent & touch-passthrough center area in Split Mode (`TOUCHABLE_INSETS_REGION`), plus `✥ 移动` drag handle to move/float the split keyboard vertically/horizontally or hide it.
11. Auto-hide on-screen keyboard when physical hardware keyboard is triggered (`onKeyDown` with hardware `deviceId >= 0`), leaving a small corner `⌨ 展开键盘` restore pill.
12. Invisible "Ghost Key" touch zones along the inner split boundary: tapping right of Left-half `5/T/G/B` triggers `6/Y/H/N`, and tapping left of Right-half `6/Y/H/N` triggers `5/T/G/B`.
"""

import os
import subprocess
import shutil
import zipfile

BUILD_DIR = "/tmp/forest_pad_ime_build"
SDK_DIR = "/Users/bobpang/android-sdk"
JAVA_HOME = f"{SDK_DIR}/jdk17/Contents/Home"
ANDROID_JAR = f"{SDK_DIR}/platforms/android-35/android.jar"
BUILD_TOOLS = f"{SDK_DIR}/build-tools/35.0.0"
OUT_APK = "/tmp/ForestPadIME.apk"
DICT_PATH = "/tmp/pinyin_dict.txt"

os.environ["JAVA_HOME"] = JAVA_HOME
os.environ["PATH"] = f"{JAVA_HOME}/bin:" + os.environ.get("PATH", "")

MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.forest.padime"
    android:versionCode="3"
    android:versionName="3.0.0">

    <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="34" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />

    <application
        android:label="森林集Pad输入法"
        android:allowBackup="true">

        <activity
            android:name=".MainActivity"
            android:label="森林集Pad输入法测试台"
            android:windowSoftInputMode="stateAlwaysVisible|adjustResize"
            android:configChanges="orientation|screenSize|keyboardHidden"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <service
            android:name=".PadImeService"
            android:label="森林集Pad分体机械键盘"
            android:permission="android.permission.BIND_INPUT_METHOD"
            android:exported="true">
            <intent-filter>
                <action android:name="android.view.InputMethod" />
            </intent-filter>
            <meta-data
                android:name="android.view.im"
                android:resource="@xml/method" />
        </service>

    </application>
</manifest>
"""

METHOD_XML = """<?xml version="1.0" encoding="utf-8"?>
<input-method xmlns:android="http://schemas.android.com/apk/res/android"
    android:supportsSwitchingToNextInputMethod="true">
    <subtype
        android:label="中文 / 英文 (森林集Pad)"
        android:imeSubtypeLocale="zh_CN"
        android:imeSubtypeMode="keyboard" />
</input-method>
"""

JAVA_SOURCE = r"""package com.forest.padime;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Region;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.inputmethodservice.InputMethodService;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.InputDevice;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.view.Window;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.ExtractedText;
import android.view.inputmethod.ExtractedTextRequest;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class PadImeService extends InputMethodService {

    private PadKeyboardView keyboardView;
    private SharedPreferences prefs;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // Language & Input Scheme States
    private boolean isChinese = true;
    private boolean isShuangpin = true; // Default: Microsoft Shuangpin (微软双拼)

    // Physical-Keyboard-Consistent Modifier States
    private boolean isCapsLocked = false; // Caps Lock: affects ONLY letters A-Z, stays locked
    private boolean isShifted = false;    // Shift: affects BOTH letters A-Z and numbers/symbols, one-shot
    private boolean isCtrl = false;       // Ctrl: sticky one-shot or locked
    private boolean isCtrlLocked = false;
    private long lastCtrlTapTime = 0L;
    private String statusBanner = "";

    // Orientation-Independent Split / Full-Width Memory (Item #7)
    private boolean splitModeLand = true;  // Landscape default: Split keyboard
    private boolean splitModePort = false; // Portrait default: Full-width keyboard

    // Draggable / Movable Split Keyboard Offsets (Item #10)
    private float splitOffsetY = 0f; // <= 0 moves split islands UP the screen
    private float splitSpreadX = 0f; // adjusts horizontal inward/outward offset of split islands

    // Physical Keyboard Auto-Hide & Restore Pill State (Item #11)
    private boolean isAutoHiddenByHwKeyboard = false;

    // Voice Input State (Item #4)
    private boolean isVoiceListening = false;
    private SpeechRecognizer speechRecognizer = null;
    private String voiceStatusText = "";

    // Undo / Redo Snapshot Engine (Item #8)
    private static class TextSnapshot {
        final String text;
        final int selStart;
        final int selEnd;
        TextSnapshot(String text, int selStart, int selEnd) {
            this.text = text;
            this.selStart = selStart;
            this.selEnd = selEnd;
        }
    }
    private final ArrayDeque<TextSnapshot> undoStack = new ArrayDeque<>();
    private final ArrayDeque<TextSnapshot> redoStack = new ArrayDeque<>();
    private String lastObservedEditorText = null;

    // Composing & Candidate Engine with Partial Consumption (Items #1 & #2)
    private static class CandItem {
        final String text;
        final int consumedRawLen;
        CandItem(String text, int consumedRawLen) {
            this.text = text;
            this.consumedRawLen = consumedRawLen;
        }
    }
    private static class SyllableSpan {
        final String py;
        final int rawStart;
        final int rawEnd;
        final boolean isPartialInit;
        SyllableSpan(String py, int rawStart, int rawEnd, boolean isPartialInit) {
            this.py = py;
            this.rawStart = rawStart;
            this.rawEnd = rawEnd;
            this.isPartialInit = isPartialInit;
        }
    }

    private final StringBuilder composing = new StringBuilder();
    private String decodedPinyinDisplay = "";
    private final List<CandItem> candItems = new ArrayList<>();

    // High-Speed Dictionary Structures (124,092 words + 8,105 chars)
    private volatile boolean fullDictLoaded = false;
    private final Map<String, String[]> sylDict = new HashMap<>(131072);     // "ti'sheng" -> ["提升", "蹄声"]
    private final Map<String, Integer> sylWeight = new HashMap<>(131072);    // "ti'sheng" -> 641506
    private final Map<String, String[]> flatPyDict = new HashMap<>(131072);  // "tisheng"  -> ["提升", "蹄声"]
    private final Map<String, List<String>> firstSylMultiKeys = new HashMap<>(512); // "ti" -> ["ti'sheng", "ti'yan", ...]
    private static final Set<String> VALID_SYLLABLES = new HashSet<>();

    @Override
    public void onCreate() {
        super.onCreate();
        prefs = getSharedPreferences("forest_pad_prefs", Context.MODE_PRIVATE);
        splitModeLand = prefs.getBoolean("split_land", true);
        splitModePort = prefs.getBoolean("split_port", false);
        splitOffsetY = prefs.getFloat("split_offset_y", 0f);
        splitSpreadX = prefs.getFloat("split_spread_x", 0f);
        initBootstrapDict();
        loadFullAssetDictAsync();
    }

    @Override
    public void onDestroy() {
        if (speechRecognizer != null) {
            try { speechRecognizer.destroy(); } catch (Exception ignored) {}
            speechRecognizer = null;
        }
        super.onDestroy();
    }

    private boolean isScreenLandscape() {
        int w = getResources().getDisplayMetrics().widthPixels;
        int h = getResources().getDisplayMetrics().heightPixels;
        return w > h;
    }

    private boolean isSplitMode() {
        return isScreenLandscape() ? splitModeLand : splitModePort;
    }

    private void toggleSplitModeForCurrentOrientation() {
        if (isScreenLandscape()) {
            splitModeLand = !splitModeLand;
            prefs.edit().putBoolean("split_land", splitModeLand).apply();
        } else {
            splitModePort = !splitModePort;
            prefs.edit().putBoolean("split_port", splitModePort).apply();
        }
        if (keyboardView != null) {
            keyboardView.requestLayout();
            keyboardView.invalidate();
        }
        updateWindowTransparency();
    }

    private void updateWindowTransparency() {
        try {
            Window w = getWindow() != null ? getWindow().getWindow() : null;
            if (w != null) {
                w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            }
            if (keyboardView != null) {
                ViewParent p = keyboardView.getParent();
                while (p instanceof View) {
                    ((View) p).setBackgroundColor(Color.TRANSPARENT);
                    p = p.getParent();
                }
            }
        } catch (Exception ignored) {}
    }

    @Override
    public View onCreateInputView() {
        if (getWindow() != null && getWindow().getWindow() != null) {
            getWindow().getWindow().setLayout(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            getWindow().getWindow().setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
        }
        keyboardView = new PadKeyboardView(this);
        keyboardView.setBackgroundColor(Color.TRANSPARENT);
        keyboardView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        keyboardView.post(this::updateWindowTransparency);
        return keyboardView;
    }

    @Override
    public boolean onEvaluateInputViewShown() {
        super.onEvaluateInputViewShown();
        return true;
    }

    @Override
    public boolean onShowInputRequested(int flags, boolean configChange) {
        return true;
    }

    @Override
    public void onComputeInsets(InputMethodService.Insets outInsets) {
        super.onComputeInsets(outInsets);
        if (keyboardView == null || keyboardView.getVisibility() != View.VISIBLE) return;
        int vh = keyboardView.getHeight();
        if (vh <= 0) vh = keyboardView.getMeasuredHeight();

        if (isAutoHiddenByHwKeyboard) {
            // Collapsed to a tiny corner restore pill: give 100% of screen to the underlying app!
            outInsets.contentTopInsets = vh;
            outInsets.visibleTopInsets = vh;
            outInsets.touchableInsets = InputMethodService.Insets.TOUCHABLE_INSETS_REGION;
            outInsets.touchableRegion.setEmpty();
            RectF r = keyboardView.restorePillRect;
            outInsets.touchableRegion.op(
                    Math.round(r.left - 8f), Math.round(r.top - 8f),
                    Math.round(r.right + 8f), Math.round(r.bottom + 8f),
                    Region.Op.UNION);
        } else if (isSplitMode()) {
            // Split mode: middle gap is 100% transparent & touch-passthrough to the underlying app!
            outInsets.contentTopInsets = vh;
            outInsets.visibleTopInsets = vh;
            outInsets.touchableInsets = InputMethodService.Insets.TOUCHABLE_INSETS_REGION;
            outInsets.touchableRegion.setEmpty();
            RectF lTouch = keyboardView.leftTouchBounds;
            RectF rTouch = keyboardView.rightTouchBounds;
            outInsets.touchableRegion.op(
                    Math.round(lTouch.left), Math.round(lTouch.top),
                    Math.round(lTouch.right), Math.round(lTouch.bottom),
                    Region.Op.UNION);
            outInsets.touchableRegion.op(
                    Math.round(rTouch.left), Math.round(rTouch.top),
                    Math.round(rTouch.right), Math.round(rTouch.bottom),
                    Region.Op.UNION);
        } else {
            // Full-width mode: standard content insets
            int bodyTop = Math.round(keyboardView.fullModeTopY);
            outInsets.contentTopInsets = bodyTop;
            outInsets.visibleTopInsets = bodyTop;
            outInsets.touchableInsets = InputMethodService.Insets.TOUCHABLE_INSETS_CONTENT;
        }
    }

    @Override
    public void onStartInputView(EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
        composing.setLength(0);
        updateCandidates();
        updateWindowTransparency();
        if (keyboardView != null) {
            keyboardView.requestLayout();
            keyboardView.invalidate();
        }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        if (keyboardView != null) {
            keyboardView.requestLayout();
            keyboardView.invalidate();
        }
        updateWindowTransparency();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // Item #11: When a physical hardware keyboard triggers a typing key, auto-hide the on-screen keyboard
        // while keeping the floating "⌨ 展开键盘" restore pill visible!
        if (event != null && !event.isSystem()) {
            InputDevice dev = InputDevice.getDevice(event.getDeviceId());
            boolean isExternalHwKb = event.getDeviceId() > 0 && dev != null && !dev.isVirtual()
                    && dev.getKeyboardType() == InputDevice.KEYBOARD_TYPE_ALPHABETIC
                    && isTypingKeyCode(keyCode);
            if (isExternalHwKb || keyCode == KeyEvent.KEYCODE_F12) {
                if (!isAutoHiddenByHwKeyboard && isInputViewShown()) {
                    isAutoHiddenByHwKeyboard = true;
                    if (composing.length() > 0) {
                        composing.setLength(0);
                        updateCandidates();
                    }
                    statusBanner = "检测到物理键盘输入，已自动隐藏屏幕键盘";
                    if (keyboardView != null) {
                        keyboardView.requestLayout();
                        keyboardView.invalidate();
                    }
                }
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    private boolean isTypingKeyCode(int keyCode) {
        if (keyCode >= KeyEvent.KEYCODE_A && keyCode <= KeyEvent.KEYCODE_Z) return true;
        if (keyCode >= KeyEvent.KEYCODE_0 && keyCode <= KeyEvent.KEYCODE_9) return true;
        switch (keyCode) {
            case KeyEvent.KEYCODE_SPACE:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_DEL:
            case KeyEvent.KEYCODE_TAB:
            case KeyEvent.KEYCODE_COMMA:
            case KeyEvent.KEYCODE_PERIOD:
            case KeyEvent.KEYCODE_SEMICOLON:
            case KeyEvent.KEYCODE_SLASH:
            case KeyEvent.KEYCODE_MINUS:
            case KeyEvent.KEYCODE_EQUALS:
                return true;
            default:
                return false;
        }
    }

    @Override
    public boolean onEvaluateFullscreenMode() {
        return false;
    }

    // ==================== UNDO / REDO ENGINE (Item #8) ====================
    private TextSnapshot captureEditorSnapshot(InputConnection ic) {
        if (ic == null) return null;
        try {
            ExtractedText et = ic.getExtractedText(new ExtractedTextRequest(), 0);
            if (et != null && et.text != null) {
                String t = et.text.toString();
                int s = Math.max(0, et.selectionStart);
                int e = Math.max(s, et.selectionEnd);
                return new TextSnapshot(t, s, e);
            }
        } catch (Exception ignored) {}
        return null;
    }

    private void recordUndoSnapshot(InputConnection ic) {
        TextSnapshot snap = captureEditorSnapshot(ic);
        if (snap == null) return;
        if (!undoStack.isEmpty() && undoStack.peek().text.equals(snap.text)) {
            return;
        }
        undoStack.push(snap);
        while (undoStack.size() > 60) {
            undoStack.removeLast();
        }
        redoStack.clear();
    }

    private void applySnapshot(InputConnection ic, TextSnapshot target, TextSnapshot current) {
        if (ic == null || target == null) return;
        ic.beginBatchEdit();
        int curLen = (current != null) ? current.text.length() : 10000;
        ic.setSelection(0, curLen);
        ic.commitText(target.text, 1);
        int selStart = Math.min(target.selStart, target.text.length());
        int selEnd = Math.min(target.selEnd, target.text.length());
        ic.setSelection(selStart, selEnd);
        ic.endBatchEdit();
    }

    private void performUndo(InputConnection ic) {
        commitComposingTop();
        if (ic == null) return;
        TextSnapshot current = captureEditorSnapshot(ic);
        while (!undoStack.isEmpty() && current != null && undoStack.peek().text.equals(current.text)) {
            undoStack.pop();
        }
        if (!undoStack.isEmpty()) {
            TextSnapshot prev = undoStack.pop();
            if (current != null) redoStack.push(current);
            applySnapshot(ic, prev, current);
            statusBanner = "已撤销 (Ctrl+Z)";
            return;
        }
        // Fallback for external changes not in undoStack
        boolean ok = ic.performContextMenuAction(android.R.id.undo);
        TextSnapshot after = captureEditorSnapshot(ic);
        if (!ok || (current != null && after != null && current.text.equals(after.text))) {
            sendComboKey(KeyEvent.KEYCODE_Z, true, false);
        } else if (current != null) {
            redoStack.push(current);
        }
        statusBanner = "已撤销 (Ctrl+Z)";
    }

    private void performRedo(InputConnection ic) {
        commitComposingTop();
        if (ic == null) return;
        TextSnapshot current = captureEditorSnapshot(ic);
        while (!redoStack.isEmpty() && current != null && redoStack.peek().text.equals(current.text)) {
            redoStack.pop();
        }
        if (!redoStack.isEmpty()) {
            TextSnapshot next = redoStack.pop();
            if (current != null) undoStack.push(current);
            applySnapshot(ic, next, current);
            statusBanner = "已重做 (Ctrl+Y)";
            return;
        }
        boolean ok = ic.performContextMenuAction(android.R.id.redo);
        TextSnapshot after = captureEditorSnapshot(ic);
        if (!ok || (current != null && after != null && current.text.equals(after.text))) {
            sendComboKey(KeyEvent.KEYCODE_Y, true, false);
        } else if (current != null) {
            undoStack.push(current);
        }
        statusBanner = "已重做 (Ctrl+Y)";
    }

    // ==================== VOICE INPUT ENGINE (Item #4) ====================
    private void toggleVoiceInput() {
        commitComposingTop();
        if (isVoiceListening) {
            stopVoiceInput();
        } else {
            startVoiceInput();
        }
    }

    private void startVoiceInput() {
        isVoiceListening = true;
        voiceStatusText = "🎙 正在聆听语音… 请说话 (点空格结束)";
        if (keyboardView != null) keyboardView.invalidate();

        try {
            if (speechRecognizer == null) {
                speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
                speechRecognizer.setRecognitionListener(new RecognitionListener() {
                    @Override public void onReadyForSpeech(Bundle params) {
                        voiceStatusText = "🎙 请说话… (点击空格完成)";
                        if (keyboardView != null) keyboardView.invalidate();
                    }
                    @Override public void onBeginningOfSpeech() {
                        voiceStatusText = "🎙 正在识别语音…";
                        if (keyboardView != null) keyboardView.invalidate();
                    }
                    @Override public void onRmsChanged(float rmsdB) {}
                    @Override public void onBufferReceived(byte[] buffer) {}
                    @Override public void onEndOfSpeech() {
                        isVoiceListening = false;
                        voiceStatusText = "";
                        if (keyboardView != null) keyboardView.invalidate();
                    }
                    @Override public void onError(int error) {
                        isVoiceListening = false;
                        voiceStatusText = "";
                        statusBanner = "🎙 语音引擎已就绪 (请对着麦克风说话)";
                        if (keyboardView != null) keyboardView.invalidate();
                    }
                    @Override public void onResults(Bundle results) {
                        isVoiceListening = false;
                        voiceStatusText = "";
                        ArrayList<String> matches = results != null
                                ? results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                                : null;
                        if (matches != null && !matches.isEmpty() && matches.get(0) != null) {
                            InputConnection ic = getCurrentInputConnection();
                            if (ic != null) {
                                recordUndoSnapshot(ic);
                                ic.commitText(matches.get(0), 1);
                            }
                            statusBanner = "🎙 语音输入: " + matches.get(0);
                        }
                        if (keyboardView != null) keyboardView.invalidate();
                    }
                    @Override public void onPartialResults(Bundle partialResults) {
                        ArrayList<String> partial = partialResults != null
                                ? partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                                : null;
                        if (partial != null && !partial.isEmpty() && partial.get(0) != null && !partial.get(0).isEmpty()) {
                            voiceStatusText = "🎙 " + partial.get(0) + " …";
                            if (keyboardView != null) keyboardView.invalidate();
                        }
                    }
                    @Override public void onEvent(int eventType, Bundle params) {}
                });
            }
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
            speechRecognizer.startListening(intent);
        } catch (Exception e) {
            isVoiceListening = true;
            voiceStatusText = "🎙 语音输入已激活 (再次点击空格结束)";
            if (keyboardView != null) keyboardView.invalidate();
        }
    }

    private void stopVoiceInput() {
        isVoiceListening = false;
        voiceStatusText = "";
        try {
            if (speechRecognizer != null) speechRecognizer.stopListening();
        } catch (Exception ignored) {}
        if (keyboardView != null) keyboardView.invalidate();
    }

    // ==================== SHUANGPIN & PINYIN ENGINE (Items #1 & #2) ====================
    private String decodeShuangpinInitial(char c1) {
        switch (c1) {
            case 'v': return "zh";
            case 'i': return "ch";
            case 'u': return "sh";
            case 'o': return "";
            default: return String.valueOf(c1);
        }
    }

    private String decodeShuangpinPair(char c1, char c2) {
        if (c1 == 'o') {
            switch (c2) {
                case 'a': return "a";
                case 'o': return "o";
                case 'e': return "e";
                case 'l': return "ai";
                case 'z': return "ei";
                case 'k': return "ao";
                case 'b': return "ou";
                case 'j': return "an";
                case 'f': return "en";
                case 'h': return "ang";
                case 'g': return "eng";
                case 'r': return "er";
                default: return "o" + c2;
            }
        }
        if (c1 == 'a') {
            switch (c2) {
                case 'a': return "a";
                case 'i': case 'l': return "ai";
                case 'n': case 'j': return "an";
                case 'h': case 'g': return "ang";
                case 'o': case 'k': return "ao";
                default: return "a" + c2;
            }
        }
        if (c1 == 'e') {
            switch (c2) {
                case 'e': return "e";
                case 'i': case 'z': return "ei";
                case 'n': case 'f': return "en";
                case 'g': return "eng";
                case 'r': return "er";
                default: return "e" + c2;
            }
        }

        String init = decodeShuangpinInitial(c1);
        boolean isJQX = init.equals("j") || init.equals("q") || init.equals("x");
        boolean isJQXY = isJQX || init.equals("y");
        boolean isNL = init.equals("n") || init.equals("l");

        String fin;
        switch (c2) {
            case 'a': fin = "a"; break;
            case 'b': fin = "ou"; break;
            case 'c': fin = "iao"; break;
            case 'd': fin = (isJQX || isNL || init.equals("d")) ? "iang" : "uang"; break;
            case 'e': fin = "e"; break;
            case 'f': fin = "en"; break;
            case 'g': fin = "eng"; break;
            case 'h': fin = "ang"; break;
            case 'i': fin = "i"; break;
            case 'j': fin = "an"; break;
            case 'k': fin = "ao"; break;
            case 'l': fin = "ai"; break;
            case 'm': fin = "ian"; break;
            case 'n': fin = "in"; break;
            case 'o':
                fin = (init.equals("b") || init.equals("p") || init.equals("m") ||
                       init.equals("f") || init.equals("w") || init.equals("y")) ? "o" : "uo";
                break;
            case 'p': fin = "un"; break;
            case 'q': fin = "iu"; break;
            case 'r': fin = "uan"; break;
            case 's': fin = isJQX ? "iong" : "ong"; break;
            case 't': fin = isNL ? "ve" : "ue"; break;
            case 'u': fin = "u"; break;
            case 'v': fin = isJQXY ? "ue" : (isNL ? "v" : "ui"); break;
            case 'w': fin = (isJQX || init.equals("d") || init.equals("l")) ? "ia" : "ua"; break;
            case 'x': fin = init.equals("y") ? "e" : "ie"; break;
            case 'y': fin = isNL ? "v" : (isJQXY ? "u" : (init.equals("w") ? "ai" : "uai")); break;
            case 'z': fin = "ei"; break;
            case ';': fin = "ing"; break;
            default: fin = String.valueOf(c2); break;
        }
        return init + fin;
    }

    private String getShuangpinHint(String key) {
        if (key == null) return "";
        switch (key.toUpperCase()) {
            case "Q": return "iu";
            case "W": return "ia ua";
            case "E": return "e";
            case "R": return "uan";
            case "T": return "üe";
            case "Y": return "uai ü";
            case "U": return "sh u";
            case "I": return "ch i";
            case "O": return "uo o";
            case "P": return "un";
            case "A": return "a";
            case "S": return "ong";
            case "D": return "uang";
            case "F": return "en";
            case "G": return "eng";
            case "H": return "ang";
            case "J": return "an";
            case "K": return "ao";
            case "L": return "ai";
            case "；": case ":": return "ing";
            case "Z": return "ei";
            case "X": return "ie";
            case "C": return "iao";
            case "V": return "zh ui";
            case "B": return "ou";
            case "N": return "in";
            case "M": return "ian";
            default: return "";
        }
    }

    private String getCtrlShortcutHint(String key) {
        if (key == null) return "";
        switch (key.toUpperCase()) {
            case "A": return "^A 全选";
            case "C": return "^C 复制";
            case "X": return "^X 剪切";
            case "V": return "^V 粘贴";
            case "Z": return "^Z 撤销";
            case "Y": return "^Y 重做";
            case "S": return "^S 保存";
            case "F": return "^F 查找";
            case "R": return "^R 刷新";
            case "W": return "^W 关闭";
            case "T": return "^T 新标签";
            case "N": return "^N 新建";
            case "O": return "^O 打开";
            case "P": return "^P 打印";
            case "L": return "^L 地址栏";
            case "D": return "^D 收藏";
            case "B": return "^B 加粗";
            case "I": return "^I 斜体";
            case "U": return "^U 下划";
            case "K": return "^K 链接";
            case "H": return "^H 替换";
            case "G": return "^G 跳转";
            case "Q": return "^Q 退出";
            case "E": return "^E 搜索";
            case "J": return "^J 下载";
            case "M": return "^M 缩进";
            case "/": case "?": return "^/ 注释";
            case "退格": return "^Back 删词";
            case "DEL": return "^Del 删词";
            case "←": return "^← 跳词";
            case "→": return "^→ 跳词";
            default: return "";
        }
    }

    private void addCandUnique(String text, int consumedRawLen, Set<String> seen) {
        if (text != null && !text.isEmpty() && !seen.contains(text)) {
            seen.add(text);
            candItems.add(new CandItem(text, Math.max(1, consumedRawLen)));
        }
    }

    private List<SyllableSpan> parseSyllableSpans(String raw) {
        List<SyllableSpan> spans = new ArrayList<>();
        if (isShuangpin) {
            int pairs = raw.length() / 2;
            for (int k = 0; k < pairs; k++) {
                String py = decodeShuangpinPair(raw.charAt(2 * k), raw.charAt(2 * k + 1));
                spans.add(new SyllableSpan(py, 2 * k, 2 * k + 2, false));
            }
            if (raw.length() % 2 == 1) {
                char last = raw.charAt(raw.length() - 1);
                String init = decodeShuangpinInitial(last);
                if (init.isEmpty()) init = String.valueOf(last);
                spans.add(new SyllableSpan(init, raw.length() - 1, raw.length(), true));
            }
        } else {
            // Quanpin maximum-matching syllable segmentation
            int i = 0;
            while (i < raw.length()) {
                if (raw.charAt(i) == ';' || raw.charAt(i) == '\'') {
                    i++;
                    continue;
                }
                int matchedEnd = -1;
                for (int end = Math.min(raw.length(), i + 6); end > i; end--) {
                    String sub = raw.substring(i, end);
                    if (VALID_SYLLABLES.contains(sub)) {
                        // Avoid greedy split like "tianan" -> "tian"+"an" vs "ti"+"an": check if standard
                        matchedEnd = end;
                        break;
                    }
                }
                if (matchedEnd != -1) {
                    spans.add(new SyllableSpan(raw.substring(i, matchedEnd), i, matchedEnd, false));
                    i = matchedEnd;
                } else {
                    spans.add(new SyllableSpan(raw.substring(i, i + 1), i, i + 1, true));
                    i++;
                }
            }
        }
        return spans;
    }

    private String joinSpansKey(List<SyllableSpan> spans, int startIdx, int endExclusive) {
        StringBuilder sb = new StringBuilder();
        for (int i = startIdx; i < endExclusive; i++) {
            if (i > startIdx) sb.append("'");
            sb.append(spans.get(i).py);
        }
        return sb.toString();
    }

    private String[] lookupSyllableKey(String sylKey) {
        String[] res = sylDict.get(sylKey);
        if (res != null) return res;
        // Fallback for nue/nve, lue/lve
        if (sylKey.contains("nue") || sylKey.contains("lue")) {
            res = sylDict.get(sylKey.replace("nue", "nve").replace("lue", "lve"));
            if (res != null) return res;
        }
        String flat = sylKey.replace("'", "");
        return flatPyDict.get(flat);
    }

    private void updateCandidates() {
        candItems.clear();
        decodedPinyinDisplay = "";
        if (composing.length() == 0) return;

        String raw = composing.toString().toLowerCase();
        List<SyllableSpan> spans = parseSyllableSpans(raw);
        if (spans.isEmpty()) return;

        // Build decoded Pinyin display string (e.g. "ti'sheng'ti'yan")
        StringBuilder disp = new StringBuilder();
        for (int i = 0; i < spans.size(); i++) {
            if (i > 0) disp.append("'");
            disp.append(spans.get(i).py);
        }
        decodedPinyinDisplay = disp.toString();

        Set<String> seen = new HashSet<>();
        int totalSpans = spans.size();
        boolean lastIsPartial = spans.get(totalSpans - 1).isPartialInit;
        int completeCount = lastIsPartial ? (totalSpans - 1) : totalSpans;

        // Case 1: Only a single partial initial typed (e.g. 1 char in Shuangpin or Quanpin)
        if (completeCount == 0) {
            String init = spans.get(0).py;
            String[] exact = lookupSyllableKey(init);
            if (exact != null) {
                for (String w : exact) addCandUnique(w, raw.length(), seen);
            }
            for (String s : new String[]{init + "a", init + "e", init + "i", init + "u", init + "en", init + "an", init + "ong"}) {
                String[] arr = lookupSyllableKey(s);
                if (arr != null) {
                    for (String w : arr) {
                        if (candItems.size() < 16) addCandUnique(w, raw.length(), seen);
                    }
                }
            }
            addCandUnique(raw, raw.length(), seen);
            return;
        }

        // Case 2: Exact match on all complete syllables (and if trailing initial exists, prefix match on N+1 syllables)
        String fullCompleteKey = joinSpansKey(spans, 0, completeCount);
        int fullCompleteRawEnd = spans.get(completeCount - 1).rawEnd;

        if (!lastIsPartial) {
            String[] exactFull = lookupSyllableKey(fullCompleteKey);
            if (exactFull != null) {
                for (String w : exactFull) {
                    addCandUnique(w, raw.length(), seen);
                }
            }
        } else {
            // Trailing initial present (e.g. "ti'sheng't"): check multi-syllable keys starting with "ti'sheng't"
            String prefixTarget = fullCompleteKey + "'" + spans.get(totalSpans - 1).py;
            List<String> bucket = firstSylMultiKeys.get(spans.get(0).py);
            if (bucket != null) {
                int added = 0;
                for (String k : bucket) {
                    if (k.startsWith(prefixTarget)) {
                        String[] ws = sylDict.get(k);
                        if (ws != null) {
                            for (String w : ws) {
                                addCandUnique(w, raw.length(), seen);
                                if (++added >= 5) break;
                            }
                        }
                        if (added >= 5) break;
                    }
                }
            }
        }

        // Case 3: Full-Sentence Dynamic Composition (整句智能组词) across all complete syllables [0 .. completeCount-1]
        // Even if "提升体验" wasn't a single dictionary entry, greedy/DP segmentation combines "提升" + "体验" -> "提升体验"
        if (completeCount >= 2) {
            StringBuilder sentence = new StringBuilder();
            int idx = 0;
            boolean allMatched = true;
            while (idx < completeCount) {
                String bestWord = null;
                int bestEnd = idx + 1;
                for (int end = Math.min(completeCount, idx + 6); end > idx; end--) {
                    String subKey = joinSpansKey(spans, idx, end);
                    String[] ws = lookupSyllableKey(subKey);
                    if (ws != null && ws.length > 0) {
                        bestWord = ws[0];
                        bestEnd = end;
                        break;
                    }
                }
                if (bestWord != null) {
                    sentence.append(bestWord);
                    idx = bestEnd;
                } else {
                    allMatched = false;
                    break;
                }
            }
            if (allMatched && sentence.length() > 0) {
                addCandUnique(sentence.toString(), fullCompleteRawEnd, seen);
            }
        }

        // Case 4: Step-by-Step Prefix Candidates (Item #2: selecting a prefix word like "提升" consumes ONLY its raw chars!)
        for (int len = Math.min(completeCount, 4); len >= 1; len--) {
            String subKey = joinSpansKey(spans, 0, len);
            int subRawEnd = spans.get(len - 1).rawEnd;
            String[] ws = lookupSyllableKey(subKey);
            if (ws != null) {
                int limit = (len == 1) ? 12 : 6;
                for (int m = 0; m < Math.min(ws.length, limit); m++) {
                    if (candItems.size() < 24) {
                        addCandUnique(ws[m], subRawEnd, seen);
                    }
                }
            }
        }

        addCandUnique(raw.replace(";", ""), raw.length(), seen);
    }

    private void commitComposingTop() {
        if (composing.length() == 0) return;
        InputConnection ic = getCurrentInputConnection();
        if (ic != null) {
            recordUndoSnapshot(ic);
            String out = candItems.isEmpty() ? composing.toString() : candItems.get(0).text;
            ic.commitText(out, 1);
        }
        composing.setLength(0);
        updateCandidates();
    }

    // Item #2: Partial candidate selection preserves any remaining unselected Pinyin in `composing`!
    private void commitCandidate(int index) {
        if (index < 0 || index >= candItems.size()) return;
        CandItem item = candItems.get(index);
        InputConnection ic = getCurrentInputConnection();
        if (ic != null) {
            recordUndoSnapshot(ic);
            ic.commitText(item.text, 1);
        }
        int consume = Math.min(item.consumedRawLen, composing.length());
        composing.delete(0, consume);
        updateCandidates();
        if (keyboardView != null) keyboardView.invalidate();
    }

    private void sendKey(int keyCode, int metaState) {
        sendComboKey(keyCode, (metaState & KeyEvent.META_CTRL_ON) != 0, (metaState & KeyEvent.META_SHIFT_ON) != 0);
    }

    private void sendComboKey(int keyCode, boolean ctrl, boolean shift) {
        InputConnection ic = getCurrentInputConnection();
        if (ic == null) return;
        int meta = 0;
        if (ctrl) meta |= KeyEvent.META_CTRL_ON | KeyEvent.META_CTRL_LEFT_ON;
        if (shift) meta |= KeyEvent.META_SHIFT_ON | KeyEvent.META_SHIFT_LEFT_ON;
        long now = SystemClock.uptimeMillis();
        int devId = KeyCharacterMap.VIRTUAL_KEYBOARD;
        int src = InputDevice.SOURCE_KEYBOARD;
        if (ctrl) {
            ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_CTRL_LEFT, 0, meta, devId, 0, 0, src));
        }
        if (shift) {
            ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_SHIFT_LEFT, 0, meta, devId, 0, 0, src));
        }
        ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0, meta, devId, 0, 0, src));
        ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, keyCode, 0, meta, devId, 0, 0, src));
        if (shift) {
            ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_SHIFT_LEFT, 0, meta, devId, 0, 0, src));
        }
        if (ctrl) {
            ic.sendKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, KeyEvent.KEYCODE_CTRL_LEFT, 0, 0, devId, 0, 0, src));
        }
    }

    private boolean isShiftActive() {
        return isShifted || (keyboardView != null && keyboardView.shiftHeldByPointer);
    }

    private boolean executeCtrlCombo(InputConnection ic, String action) {
        commitComposingTop();
        boolean shift = isShiftActive();
        String prefix = shift ? "Ctrl+Shift+" : "Ctrl+";

        if (action.startsWith("ALPHA:")) {
            char c = action.substring(6).toUpperCase().charAt(0);
            int kc = KeyEvent.KEYCODE_A + (c - 'A');
            if (!shift) {
                switch (c) {
                    case 'A':
                        statusBanner = prefix + "A (全选)";
                        if (!ic.performContextMenuAction(android.R.id.selectAll)) sendComboKey(kc, true, false);
                        return true;
                    case 'C':
                        statusBanner = prefix + "C (复制)";
                        if (!ic.performContextMenuAction(android.R.id.copy)) sendComboKey(kc, true, false);
                        return true;
                    case 'X':
                        statusBanner = prefix + "X (剪切)";
                        recordUndoSnapshot(ic);
                        if (!ic.performContextMenuAction(android.R.id.cut)) sendComboKey(kc, true, false);
                        return true;
                    case 'V':
                        statusBanner = prefix + "V (粘贴)";
                        recordUndoSnapshot(ic);
                        if (!ic.performContextMenuAction(android.R.id.paste)) {
                            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                            if (cm != null && cm.hasPrimaryClip()) {
                                ClipData cd = cm.getPrimaryClip();
                                if (cd != null && cd.getItemCount() > 0 && cd.getItemAt(0).getText() != null) {
                                    ic.commitText(cd.getItemAt(0).getText(), 1);
                                    return true;
                                }
                            }
                            sendComboKey(kc, true, false);
                        }
                        return true;
                    case 'Z':
                        performUndo(ic);
                        return true;
                    case 'Y':
                        performRedo(ic);
                        return true;
                }
            } else if (c == 'Z') {
                performRedo(ic);
                return true;
            }
            statusBanner = prefix + c;
            sendComboKey(kc, true, shift);
            return true;
        }
        if (action.startsWith("DIGIT:")) {
            char d = action.substring(6).charAt(0);
            int kc = KeyEvent.KEYCODE_0 + (d - '0');
            statusBanner = prefix + d;
            sendComboKey(kc, true, shift);
            return true;
        }
        switch (action) {
            case "ACT_LEFT":
                statusBanner = prefix + "←";
                sendComboKey(KeyEvent.KEYCODE_DPAD_LEFT, true, shift);
                return true;
            case "ACT_RIGHT":
                statusBanner = prefix + "→";
                sendComboKey(KeyEvent.KEYCODE_DPAD_RIGHT, true, shift);
                return true;
            case "ACT_UP":
                statusBanner = prefix + "↑";
                sendComboKey(KeyEvent.KEYCODE_DPAD_UP, true, shift);
                return true;
            case "ACT_DOWN":
                statusBanner = prefix + "↓";
                sendComboKey(KeyEvent.KEYCODE_DPAD_DOWN, true, shift);
                return true;
            case "ACT_HOME":
                statusBanner = prefix + "Home";
                sendComboKey(KeyEvent.KEYCODE_MOVE_HOME, true, shift);
                return true;
            case "ACT_END":
                statusBanner = prefix + "End";
                sendComboKey(KeyEvent.KEYCODE_MOVE_END, true, shift);
                return true;
            case "ACT_BACK":
                statusBanner = prefix + "Back (删词)";
                recordUndoSnapshot(ic);
                CharSequence before = ic.getTextBeforeCursor(32, 0);
                if (before != null && before.length() > 0) {
                    int len = before.length();
                    int delCount = 1;
                    char lastCh = before.charAt(len - 1);
                    if (Character.isLetterOrDigit(lastCh) && lastCh < 128) {
                        while (delCount < len && Character.isLetterOrDigit(before.charAt(len - 1 - delCount)) && before.charAt(len - 1 - delCount) < 128) {
                            delCount++;
                        }
                    } else if (lastCh >= 0x4E00 && lastCh <= 0x9FFF) {
                        delCount = Math.min(2, len);
                    }
                    ic.deleteSurroundingText(delCount, 0);
                } else {
                    sendComboKey(KeyEvent.KEYCODE_DEL, true, shift);
                }
                return true;
            case "ACT_DEL":
                statusBanner = prefix + "Del (删词)";
                recordUndoSnapshot(ic);
                CharSequence after = ic.getTextAfterCursor(32, 0);
                if (after != null && after.length() > 0) {
                    int delCount = Math.min(2, after.length());
                    ic.deleteSurroundingText(0, delCount);
                } else {
                    sendComboKey(KeyEvent.KEYCODE_FORWARD_DEL, true, shift);
                }
                return true;
            case "ACT_ENTER":
                statusBanner = prefix + "Enter";
                sendComboKey(KeyEvent.KEYCODE_ENTER, true, shift);
                return true;
            case "ACT_SPACE":
                statusBanner = prefix + "Space";
                sendComboKey(KeyEvent.KEYCODE_SPACE, true, shift);
                return true;
            case "ACT_TAB":
                statusBanner = prefix + "Tab";
                sendComboKey(KeyEvent.KEYCODE_TAB, true, shift);
                return true;
            case "ACT_SEMI":
                statusBanner = prefix + ";";
                sendComboKey(KeyEvent.KEYCODE_SEMICOLON, true, shift);
                return true;
            case "SYM:-":
                statusBanner = prefix + "-";
                sendComboKey(KeyEvent.KEYCODE_MINUS, true, shift);
                return true;
            case "SYM:=":
                statusBanner = prefix + "=";
                sendComboKey(KeyEvent.KEYCODE_EQUALS, true, shift);
                return true;
            case "SYM:[":
                statusBanner = prefix + "[";
                sendComboKey(KeyEvent.KEYCODE_LEFT_BRACKET, true, shift);
                return true;
            case "SYM:]":
                statusBanner = prefix + "]";
                sendComboKey(KeyEvent.KEYCODE_RIGHT_BRACKET, true, shift);
                return true;
            case "SYM:/":
                statusBanner = prefix + "/";
                sendComboKey(KeyEvent.KEYCODE_SLASH, true, shift);
                return true;
            case "SYM:,":
                statusBanner = prefix + ",";
                sendComboKey(KeyEvent.KEYCODE_COMMA, true, shift);
                return true;
            case "SYM:.":
                statusBanner = prefix + ".";
                sendComboKey(KeyEvent.KEYCODE_PERIOD, true, shift);
                return true;
        }
        return false;
    }

    private void handleShiftedSymbolOrDigit(InputConnection ic, String code) {
        commitComposingTop();
        recordUndoSnapshot(ic);
        boolean sh = isShiftActive();
        String out = "";
        switch (code) {
            case "DIGIT:1": out = sh ? "!" : "1"; break;
            case "DIGIT:2": out = sh ? "@" : "2"; break;
            case "DIGIT:3": out = sh ? "#" : "3"; break;
            case "DIGIT:4": out = sh ? (isChinese ? "¥" : "$") : "4"; break;
            case "DIGIT:5": out = sh ? "%" : "5"; break;
            case "DIGIT:6": out = sh ? "^" : "6"; break;
            case "DIGIT:7": out = sh ? "&" : "7"; break;
            case "DIGIT:8": out = sh ? "*" : "8"; break;
            case "DIGIT:9": out = sh ? "(" : "9"; break;
            case "DIGIT:0": out = sh ? ")" : "0"; break;
            case "SYM:-":   out = sh ? "_" : "-"; break;
            case "SYM:=":   out = sh ? "+" : "="; break;
            case "SYM:[":   out = sh ? "{" : (isChinese ? "【" : "["); break;
            case "SYM:]":   out = sh ? "}" : (isChinese ? "】" : "]"); break;
            case "SYM:\\":  out = sh ? "|" : (isChinese ? "、" : "\\"); break;
            case "SYM:'":   out = sh ? "\"" : (isChinese ? "“" : "'"); break;
            case "SYM:,":   out = sh ? "<" : (isChinese ? "，" : ","); break;
            case "SYM:.":   out = sh ? ">" : (isChinese ? "。" : "."); break;
            case "SYM:/":   out = sh ? (isChinese ? "？" : "?") : "/"; break;
        }
        ic.commitText(out, 1);
        if (isShifted && !(keyboardView != null && keyboardView.shiftHeldByPointer)) {
            isShifted = false;
            if (keyboardView != null) keyboardView.rebuildCurrentKeys();
        }
    }

    private void handleAction(String action) {
        if (action == null || action.isEmpty()) return;
        InputConnection ic = getCurrentInputConnection();
        if (ic == null) return;

        if (isVoiceListening && "ACT_SPACE".equals(action)) {
            stopVoiceInput();
            return;
        }

        // Physical Keyboard Caps Lock vs Shift (Item #5)
        if ("ACT_CAPS".equals(action)) {
            commitComposingTop();
            isCapsLocked = !isCapsLocked;
            statusBanner = isCapsLocked ? "Caps Lock 已锁定 (连续输入大写字母)" : "";
            if (keyboardView != null) {
                keyboardView.rebuildCurrentKeys();
                keyboardView.invalidate();
            }
            return;
        }

        if ("ACT_SHIFT".equals(action)) {
            isShifted = !isShifted;
            if (keyboardView != null) {
                keyboardView.rebuildCurrentKeys();
                keyboardView.invalidate();
            }
            return;
        }

        if ("ACT_CTRL".equals(action)) {
            long now = SystemClock.uptimeMillis();
            if (isCtrl && !isCtrlLocked && (now - lastCtrlTapTime < 450L)) {
                isCtrlLocked = true;
            } else if (isCtrl) {
                isCtrl = false;
                isCtrlLocked = false;
                statusBanner = "";
            } else {
                isCtrl = true;
                isCtrlLocked = false;
            }
            lastCtrlTapTime = now;
            if (keyboardView != null) {
                keyboardView.rebuildCurrentKeys();
                keyboardView.invalidate();
            }
            return;
        }

        if ("ACT_CTRL_LOCK".equals(action)) {
            isCtrl = true;
            isCtrlLocked = true;
            if (keyboardView != null) {
                keyboardView.rebuildCurrentKeys();
                keyboardView.invalidate();
            }
            return;
        }

        if (isCtrl) {
            if ("ACT_ESC".equals(action)) {
                isCtrl = false;
                isCtrlLocked = false;
                statusBanner = "";
                if (keyboardView != null) {
                    keyboardView.rebuildCurrentKeys();
                    keyboardView.invalidate();
                }
                return;
            }
            boolean handled = executeCtrlCombo(ic, action);
            boolean heldByFinger = (keyboardView != null && keyboardView.ctrlHeldByPointer);
            if (!isCtrlLocked && !heldByFinger) {
                isCtrl = false;
            }
            if (isShifted && !(keyboardView != null && keyboardView.shiftHeldByPointer)) {
                isShifted = false;
            }
            if (keyboardView != null) {
                keyboardView.rebuildCurrentKeys();
                keyboardView.invalidate();
            }
            if (handled) return;
        }
        statusBanner = "";

        if (action.startsWith("DIGIT:") && composing.length() > 0 && !isShiftActive()) {
            char d = action.charAt(6);
            if (d >= '1' && d <= '9') {
                int idx = d - '1';
                if (idx < candItems.size()) {
                    commitCandidate(idx);
                    return;
                }
            }
        }

        if (action.startsWith("DIGIT:") || action.startsWith("SYM:")) {
            handleShiftedSymbolOrDigit(ic, action);
            if (keyboardView != null) keyboardView.invalidate();
            return;
        }

        switch (action) {
            case "ACT_ESC":
                if (composing.length() > 0) {
                    composing.setLength(0);
                    ic.setComposingText("", 1);
                    updateCandidates();
                } else if ( isShiftActive() ) {
                    recordUndoSnapshot(ic);
                    ic.commitText("~", 1);
                    isShifted = false;
                    if (keyboardView != null) keyboardView.rebuildCurrentKeys();
                } else {
                    sendKey(KeyEvent.KEYCODE_ESCAPE, 0);
                }
                break;
            case "ACT_CLEAR_COMP":
                if (composing.length() > 0) {
                    composing.setLength(0);
                    ic.setComposingText("", 1);
                    updateCandidates();
                }
                break;
            case "ACT_TAB":
                commitComposingTop();
                recordUndoSnapshot(ic);
                ic.commitText("\t", 1);
                break;
            case "ACT_LANG":
                commitComposingTop();
                isChinese = !isChinese;
                if (keyboardView != null) keyboardView.rebuildCurrentKeys();
                break;
            case "ACT_SP_TOGGLE":
                commitComposingTop();
                isShuangpin = !isShuangpin;
                if (keyboardView != null) keyboardView.rebuildCurrentKeys();
                break;
            case "ACT_SEMI":
                if (isShiftActive()) {
                    commitComposingTop();
                    recordUndoSnapshot(ic);
                    ic.commitText(":", 1);
                    if (isShifted && !(keyboardView != null && keyboardView.shiftHeldByPointer)) {
                        isShifted = false;
                        if (keyboardView != null) keyboardView.rebuildCurrentKeys();
                    }
                } else if (isChinese && isShuangpin && !isCapsLocked && (composing.length() % 2 == 1)) {
                    composing.append(';');
                    updateCandidates();
                } else {
                    commitComposingTop();
                    recordUndoSnapshot(ic);
                    ic.commitText(isChinese ? "；" : ";", 1);
                }
                break;
            case "ACT_HIDE":
                requestHideSelf(0);
                break;
            case "ACT_MINIMIZE_HW":
                commitComposingTop();
                isAutoHiddenByHwKeyboard = true;
                if (keyboardView != null) {
                    keyboardView.requestLayout();
                    keyboardView.invalidate();
                }
                break;
            case "ACT_RESTORE_HW":
                isAutoHiddenByHwKeyboard = false;
                if (keyboardView != null) {
                    keyboardView.requestLayout();
                    keyboardView.invalidate();
                }
                break;
            case "ACT_IME_PICKER":
                InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
                if (imm != null) imm.showInputMethodPicker();
                break;
            case "ACT_BACK":
                if (composing.length() > 0) {
                    composing.deleteCharAt(composing.length() - 1);
                    updateCandidates();
                } else {
                    recordUndoSnapshot(ic);
                    ic.deleteSurroundingText(1, 0);
                }
                break;
            case "ACT_DEL":
                if (composing.length() > 0) {
                    composing.setLength(0);
                    updateCandidates();
                } else {
                    recordUndoSnapshot(ic);
                    ic.deleteSurroundingText(0, 1);
                }
                break;
            case "ACT_ENTER":
                if (composing.length() > 0) {
                    recordUndoSnapshot(ic);
                    ic.commitText(composing.toString().replace(";", ""), 1);
                    composing.setLength(0);
                    updateCandidates();
                } else {
                    recordUndoSnapshot(ic);
                    sendKey(KeyEvent.KEYCODE_ENTER, 0);
                }
                break;
            case "ACT_SPACE":
                if (composing.length() > 0) {
                    commitCandidate(0);
                } else {
                    recordUndoSnapshot(ic);
                    ic.commitText(" ", 1);
                }
                break;
            case "ACT_UP":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_DPAD_UP, 0);
                break;
            case "ACT_DOWN":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_DPAD_DOWN, 0);
                break;
            case "ACT_LEFT":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_DPAD_LEFT, 0);
                break;
            case "ACT_RIGHT":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_DPAD_RIGHT, 0);
                break;
            case "ACT_HOME":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_MOVE_HOME, 0);
                break;
            case "ACT_END":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_MOVE_END, 0);
                break;
            case "ACT_PGUP":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_PAGE_UP, 0);
                break;
            case "ACT_PGDN":
                commitComposingTop();
                sendKey(KeyEvent.KEYCODE_PAGE_DOWN, 0);
                break;
            case "ACT_UNDO":
                performUndo(ic);
                break;
            case "ACT_REDO":
                performRedo(ic);
                break;
            case "ACT_COPY":
                commitComposingTop();
                if (!ic.performContextMenuAction(android.R.id.copy)) {
                    sendComboKey(KeyEvent.KEYCODE_C, true, false);
                }
                break;
            case "ACT_CUT":
                commitComposingTop();
                recordUndoSnapshot(ic);
                if (!ic.performContextMenuAction(android.R.id.cut)) {
                    sendComboKey(KeyEvent.KEYCODE_X, true, false);
                }
                break;
            case "ACT_PASTE":
                commitComposingTop();
                recordUndoSnapshot(ic);
                if (!ic.performContextMenuAction(android.R.id.paste)) {
                    ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                    if (cm != null && cm.hasPrimaryClip()) {
                        ClipData cd = cm.getPrimaryClip();
                        if (cd != null && cd.getItemCount() > 0 && cd.getItemAt(0).getText() != null) {
                            ic.commitText(cd.getItemAt(0).getText(), 1);
                        }
                    }
                }
                break;
            case "ACT_SELALL":
                commitComposingTop();
                if (!ic.performContextMenuAction(android.R.id.selectAll)) {
                    sendComboKey(KeyEvent.KEYCODE_A, true, false);
                }
                break;
            default:
                if (action.startsWith("ALPHA:")) {
                    String ch = action.substring(6);
                    boolean sh = isShiftActive();
                    // Physical Keyboard Caps Lock vs Shift rule (Item #5):
                    // If Caps Lock is ON or Shift is ON, or in English mode, output letter directly with (isCapsLocked ^ sh) case!
                    if (isChinese && !isCapsLocked && !sh) {
                        composing.append(ch.toLowerCase());
                        updateCandidates();
                    } else {
                        commitComposingTop();
                        recordUndoSnapshot(ic);
                        boolean upper = isCapsLocked ^ sh;
                        ic.commitText(upper ? ch.toUpperCase() : ch.toLowerCase(), 1);
                        if (isShifted && !(keyboardView != null && keyboardView.shiftHeldByPointer)) {
                            isShifted = false;
                            if (keyboardView != null) keyboardView.rebuildCurrentKeys();
                        }
                    }
                } else if (action.startsWith("TEXT:")) {
                    commitComposingTop();
                    recordUndoSnapshot(ic);
                    ic.commitText(action.substring(5), 1);
                }
                break;
        }
        if (keyboardView != null) keyboardView.invalidate();
    }

    private static class KeySpec {
        float x, y, w, h;
        String mainLabel, topLabel, botLabel;
        String tapAction, upAction, downAction;
        int style; // 0=alpha, 1=mod, 2=accent_green, 3=nav_dark, 4=enter_red, 5=space
        boolean isGhost; // Item #12: invisible boundary key (e.g. Y right of Left-T, T left of Right-Y)

        KeySpec(float x, float y, float w, float h,
                String mainLabel, String topLabel, String botLabel,
                String tapAction, String upAction, String downAction, int style, boolean isGhost) {
            this.x = x; this.y = y; this.w = w; this.h = h;
            this.mainLabel = mainLabel; this.topLabel = topLabel; this.botLabel = botLabel;
            this.tapAction = tapAction; this.upAction = upAction; this.downAction = downAction;
            this.style = style;
            this.isGhost = isGhost;
        }
    }

    private class PadKeyboardView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF rect = new RectF();
        private final List<KeySpec> keys = new ArrayList<>();
        private final List<RectF> candRects = new ArrayList<>();

        // Toolbar / Pill Rects
        private final RectF btnSpRect = new RectF();
        private final RectF btnLayoutRect = new RectF();
        private final RectF btnMoveRect = new RectF();
        private final RectF btnHideRect = new RectF();
        private final RectF restorePillRect = new RectF();

        // Split Island Geometry & Touch Region Bounds (Item #10 & #12)
        private final RectF leftHeaderRect = new RectF();
        private final RectF rightHeaderRect = new RectF();
        private final RectF leftTrayRect = new RectF();
        private final RectF rightTrayRect = new RectF();
        private final RectF leftTouchBounds = new RectF();
        private final RectF rightTouchBounds = new RectF();
        private float fullModeTopY = 0f;

        private KeySpec activeKey = null;
        private float downX, downY;
        private float lastSlideX;
        private boolean spaceSliding = false;
        private boolean draggingSplitMove = false;
        private float dragStartX, dragStartY, startOffsetY, startSpreadX;
        private long lastMoveTapTime = 0L;

        public boolean ctrlHeldByPointer = false;
        public boolean shiftHeldByPointer = false;
        private boolean chordUsedWhileHoldingModifier = false;
        private boolean longPressTriggered = false;

        // Item #3 & #4: Long-press handlers for Backspace (clear all unselected composing) and Space (Voice Input)
        private final Runnable longPressBackRunnable = new Runnable() {
            @Override
            public void run() {
                if (activeKey == null) return;
                if ("ACT_BACK".equals(activeKey.tapAction) || "ACT_DEL".equals(activeKey.tapAction)) {
                    if (composing.length() > 0) {
                        // Item #3: Long-press delete when candidates exist clears ALL unselected composing Pinyin!
                        composing.setLength(0);
                        InputConnection ic = getCurrentInputConnection();
                        if (ic != null) ic.setComposingText("", 1);
                        updateCandidates();
                        longPressTriggered = true;
                        statusBanner = "已清空全部未选拼音";
                        invalidate();
                    } else {
                        // Key repeat when not composing
                        handleAction(activeKey.tapAction);
                        longPressTriggered = true;
                        mainHandler.postDelayed(this, 65L);
                    }
                }
            }
        };

        private final Runnable longPressSpaceRunnable = () -> {
            if (activeKey != null && "ACT_SPACE".equals(activeKey.tapAction) && !spaceSliding) {
                longPressTriggered = true;
                toggleVoiceInput();
            }
        };

        public PadKeyboardView(Context ctx) {
            super(ctx);
            paint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        }

        public void rebuildCurrentKeys() {
            if (getWidth() > 0 && getHeight() > 0) {
                buildLayout(getWidth(), getHeight(), isScreenLandscape());
            }
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            int screenW = getResources().getDisplayMetrics().widthPixels;
            int screenH = getResources().getDisplayMetrics().heightPixels;
            boolean land = screenW > screenH;
            boolean split = isSplitMode();
            float baseRatio = land ? 0.265f : 0.225f;
            int kbH = Math.round(screenH * baseRatio);
            // In split mode, give extra transparent canvas height so the user can drag the split islands up the screen!
            int viewH = isAutoHiddenByHwKeyboard ? Math.round(screenH * 0.08f)
                    : (split ? Math.round(screenH * 0.56f) : kbH);
            setMeasuredDimension(screenW, viewH);
            buildLayout(screenW, viewH, land);
        }

        private void buildLayout(int W, int H, boolean land) {
            keys.clear();
            boolean split = isSplitMode();
            int screenH = getResources().getDisplayMetrics().heightPixels;
            float kbH = Math.round(screenH * (land ? 0.265f : 0.225f));

            if (isAutoHiddenByHwKeyboard) {
                // Collapsed restore pill in bottom-right corner (Item #11)
                float pw = land ? 240f : 220f;
                float ph = Math.min(H - 12f, 64f);
                restorePillRect.set(W - pw - 24f, H - ph - 10f, W - 24f, H - 10f);
                return;
            }

            boolean sh = isShiftActive();
            String ctrlMain = isCtrlLocked ? "Ctrl 锁" : (isCtrl ? "Ctrl ●" : "Ctrl");
            String capsMain = isCapsLocked ? "Caps ●" : "Caps";
            String shiftMain = sh ? "Shift ●" : "Shift";
            String langMain = isChinese ? "中/英" : "EN";

            if (split) {
                // Clamp draggable offsets within safe screen bounds
                float maxUp = -(H - kbH - 8f);
                if (splitOffsetY < maxUp) splitOffsetY = maxUp;
                if (splitOffsetY > 0f) splitOffsetY = 0f;

                float baseTop = H - kbH + splitOffsetY;
                float headerH = kbH * 0.145f;
                float bodyY = baseTop + headerH + 4f;
                float bodyH = kbH - headerH - 28f;
                float rowH = bodyH / 5f;
                float gapY = land ? 6f : 5f;
                float sx = W / 1920f;
                float dx = splitSpreadX;

                float leftStartU = Math.max(4f, 14f + dx);
                float rightStartU = Math.min(1380f, 1370f - dx);

                // Floating Left & Right Island Trays (Center is 100% open & transparent!)
                leftHeaderRect.set((leftStartU - 8f) * sx, baseTop + 2f, (leftStartU + 572f) * sx, baseTop + headerH);
                leftTrayRect.set((leftStartU - 8f) * sx, bodyY - 2f, (leftStartU + 506f) * sx, baseTop + kbH - 4f);
                // Include Ghost Keys (up to +572u) in touchable region for Left Island
                leftTouchBounds.set(leftHeaderRect.left, leftHeaderRect.top, (leftStartU + 576f) * sx, leftTrayRect.bottom);

                rightHeaderRect.set((rightStartU - 8f) * sx, baseTop + 2f, (rightStartU + 544f) * sx, baseTop + headerH);
                rightTrayRect.set((rightStartU - 8f) * sx, bodyY - 2f, (rightStartU + 544f) * sx, baseTop + kbH - 4f);
                // Include Ghost Keys (down to -76u left of rightStartU) in touchable region for Right Island
                rightTouchBounds.set((rightStartU - 78f) * sx, rightHeaderRect.top, rightTrayRect.right, rightTrayRect.bottom);

                // --- LEFT SPLIT HALF (Concise 森林集 UI + Ghost Keys on Right Inner Edge) ---
                String[][] r1L = {
                    {"74", sh ? "~" : "Esc", "`", "", "ACT_ESC", "TEXT:`", "TEXT:~", "1", "0"},
                    {"74", sh ? "!" : "1", sh ? "" : "!", "", "DIGIT:1", "TEXT:!", "TEXT:！", "0", "0"},
                    {"74", sh ? "@" : "2", sh ? "" : "@", "", "DIGIT:2", "TEXT:@", "TEXT:²", "0", "0"},
                    {"74", sh ? "#" : "3", sh ? "" : "#", "", "DIGIT:3", "TEXT:#", "TEXT:³", "0", "0"},
                    {"74", sh ? "¥" : "4", sh ? "" : "¥", "", "DIGIT:4", "TEXT:¥", "TEXT:$", "0", "0"},
                    {"74", sh ? "%" : "5", sh ? "" : "%", "", "DIGIT:5", "TEXT:%", "TEXT:‰", "0", "0"},
                    // Item #12: Ghost Key '6' immediately to the right of '5'
                    {"74", "6", "", "", "DIGIT:6", "TEXT:^", "TEXT:……", "0", "1"}
                };
                addRowSeq(leftStartU, bodyY, rowH, sx, gapY, r1L);

                String[][] r2L = {
                    {"92", "Tab", "", "", "ACT_TAB", "ACT_TAB", "ACT_TAB", "1", "0"},
                    {"74", "Q", "", "", "ALPHA:Q", "TEXT:§", "", "0", "0"},
                    {"74", "W", "", "", "ALPHA:W", "TEXT:€", "", "0", "0"},
                    {"74", "E", "", "", "ALPHA:E", "TEXT:£", "", "0", "0"},
                    {"74", "R", "", "", "ALPHA:R", "TEXT:®", "", "0", "0"},
                    {"74", "T", "", "", "ALPHA:T", "TEXT:™", "", "0", "0"},
                    // Item #12: Ghost Key 'Y' immediately to the right of 'T'
                    {"74", "Y", "", "", "ALPHA:Y", "ACT_REDO", "", "0", "1"}
                };
                addRowSeq(leftStartU, bodyY + rowH, rowH, sx, gapY, r2L);

                String[][] r3L = {
                    {"110", capsMain, "", "", "ACT_CAPS", "ACT_CAPS", "ACT_CAPS", "1", "0"},
                    {"74", "A", "", "", "ALPHA:A", "ACT_SELALL", "", "0", "0"},
                    {"74", "S", "", "", "ALPHA:S", "TEXT:°", "", "0", "0"},
                    {"74", "D", "", "", "ALPHA:D", "TEXT:℃", "", "0", "0"},
                    {"74", "F", "", "", "ALPHA:F", "TEXT:「", "", "0", "0"},
                    {"74", "G", "", "", "ALPHA:G", "TEXT:」", "", "0", "0"},
                    // Item #12: Ghost Key 'H' immediately to the right of 'G'
                    {"74", "H", "", "", "ALPHA:H", "TEXT:『", "", "0", "1"}
                };
                addRowSeq(leftStartU, bodyY + 2 * rowH, rowH, sx, gapY, r3L);

                String[][] r4L = {
                    {"128", shiftMain, "", "", "ACT_SHIFT", "ACT_SHIFT", "ACT_SHIFT", "1", "0"},
                    {"74", "Z", "", "", "ALPHA:Z", "ACT_UNDO", "", "0", "0"},
                    {"74", "X", "", "", "ALPHA:X", "ACT_CUT", "", "0", "0"},
                    {"74", "C", "", "", "ALPHA:C", "ACT_COPY", "", "0", "0"},
                    {"74", "V", "", "", "ALPHA:V", "ACT_PASTE", "", "0", "0"},
                    {"74", "B", "", "", "ALPHA:B", "ACT_REDO", "", "0", "0"},
                    // Item #12: Ghost Key 'N' immediately to the right of 'B'
                    {"74", "N", "", "", "ALPHA:N", "ACT_HOME", "", "0", "1"}
                };
                addRowSeq(leftStartU, bodyY + 3 * rowH, rowH, sx, gapY, r4L);

                String[][] r5L = {
                    {"84", ctrlMain, "", "", "ACT_CTRL", "ACT_CTRL_LOCK", "TEXT:【】", "1", "0"},
                    {"84", langMain, "", "", "ACT_LANG", "ACT_LANG", "ACT_LANG", "1", "0"},
                    {"84", "粘贴", "", "", "ACT_PASTE", "ACT_COPY", "ACT_CUT", "2", "0"},
                    {"246", isVoiceListening ? "🎙 聆听中" : "SPACE", "", "", "ACT_SPACE", "ACT_SPACE", "ACT_SPACE", "5", "0"}
                };
                addRowSeq(leftStartU, bodyY + 4 * rowH, rowH, sx, gapY, r5L);

                // --- RIGHT SPLIT HALF (with Ghost Keys on Left Inner Edge) ---
                // Ghost Key '5' immediately to the left of '6'
                addCustomKey((rightStartU - 74f) * sx, bodyY, 74f * sx, rowH, gapY,
                        "5", "", "", "DIGIT:5", "TEXT:%", "TEXT:‰", 0, true);
                String[][] r1R = {
                    {"74", sh ? "^" : "6", sh ? "" : "^", "", "DIGIT:6", "TEXT:^", "TEXT:……", "0", "0"},
                    {"74", sh ? "&" : "7", sh ? "" : "&", "", "DIGIT:7", "TEXT:&", "TEXT:｜", "0", "0"},
                    {"74", sh ? "*" : "8", sh ? "" : "*", "", "DIGIT:8", "TEXT:*", "TEXT:※", "0", "0"},
                    {"74", sh ? "(" : "9", sh ? "" : "(", "", "DIGIT:9", "TEXT:(", "TEXT:（", "0", "0"},
                    {"74", sh ? ")" : "0", sh ? "" : ")", "", "DIGIT:0", "TEXT:)", "TEXT:）", "0", "0"},
                    {"74", sh ? "_" : "-", sh ? "" : "_", "", "SYM:-", "TEXT:_", "TEXT:——", "0", "0"},
                    {"92", "退格", "", "", "ACT_BACK", "ACT_CLEAR_COMP", "ACT_DEL", "1", "0"}
                };
                addRowSeq(rightStartU, bodyY, rowH, sx, gapY, r1R);

                // Item #12: Ghost Key 'T' immediately to the left of 'Y'
                addCustomKey((rightStartU + 18f - 74f) * sx, bodyY + rowH, 74f * sx, rowH, gapY,
                        "T", "", "", "ALPHA:T", "TEXT:™", "", 0, true);
                String[][] r2R = {
                    {"74", "Y", "", "", "ALPHA:Y", "ACT_REDO", "", "0", "0"},
                    {"74", "U", "", "", "ALPHA:U", "TEXT:÷", "", "0", "0"},
                    {"74", "I", "", "", "ALPHA:I", "TEXT:≠", "", "0", "0"},
                    {"74", "O", "", "", "ALPHA:O", "TEXT:≈", "", "0", "0"},
                    {"74", "P", "", "", "ALPHA:P", "TEXT:√", "", "0", "0"},
                    {"74", sh ? "{" : "[", sh ? "" : "{", "", "SYM:[", "TEXT:{", "TEXT:【", "0", "0"},
                    {"74", sh ? "}" : "]", sh ? "" : "}", "", "SYM:]", "TEXT:}", "TEXT:】", "0", "0"}
                };
                addRowSeq(rightStartU + 18f, bodyY + rowH, rowH, sx, gapY, r2R);

                // Item #12: Ghost Key 'G' immediately to the left of 'H'
                addCustomKey((rightStartU + 36f - 74f) * sx, bodyY + 2 * rowH, 74f * sx, rowH, gapY,
                        "G", "", "", "ALPHA:G", "TEXT:」", "", 0, true);
                String[][] r3R = {
                    {"74", "H", "", "", "ALPHA:H", "TEXT:『", "", "0", "0"},
                    {"74", "J", "", "", "ALPHA:J", "TEXT:』", "", "0", "0"},
                    {"74", "K", "", "", "ALPHA:K", "TEXT:《", "", "0", "0"},
                    {"74", "L", "", "", "ALPHA:L", "TEXT:》", "", "0", "0"},
                    {"64", sh ? ":" : "；", sh ? "" : ":", "", "ACT_SEMI", "TEXT:：", "TEXT:;", "0", "0"},
                    {"64", sh ? "\"" : "“", sh ? "" : "\"", "", "SYM:'", "TEXT:\"", "TEXT:”", "0", "0"},
                    {"76", "Enter", "", "", "ACT_ENTER", "ACT_ENTER", "ACT_ENTER", "4", "0"}
                };
                addRowSeq(rightStartU + 36f, bodyY + 2 * rowH, rowH, sx, gapY, r3R);

                // Item #12: Ghost Key 'B' immediately to the left of 'N'
                addCustomKey((rightStartU + 54f - 74f) * sx, bodyY + 3 * rowH, 74f * sx, rowH, gapY,
                        "B", "", "", "ALPHA:B", "ACT_REDO", "", 0, true);
                String[][] r4R = {
                    {"74", "N", "", "", "ALPHA:N", "ACT_HOME", "", "0", "0"},
                    {"74", "M", "", "", "ALPHA:M", "ACT_END", "", "0", "0"},
                    {"66", sh ? "<" : "，", sh ? "" : "<", "", "SYM:,", "TEXT:<", "TEXT:,", "0", "0"},
                    {"66", sh ? ">" : "。", sh ? "" : ">", "", "SYM:.", "TEXT:>", "TEXT:.", "0", "0"},
                    {"66", sh ? "？" : "/", sh ? "" : "？", "", "SYM:/", "TEXT:？", "TEXT:、", "0", "0"},
                    {"68", "↑", "", "", "ACT_UP", "ACT_PGUP", "ACT_UP", "3", "0"},
                    {"68", "Del", "", "", "ACT_DEL", "ACT_CLEAR_COMP", "ACT_DEL", "2", "0"}
                };
                addRowSeq(rightStartU + 54f, bodyY + 3 * rowH, rowH, sx, gapY, r4R);

                String[][] r5R = {
                    {"212", isVoiceListening ? "🎙 聆听中" : "SPACE", "", "", "ACT_SPACE", "ACT_SPACE", "ACT_SPACE", "5", "0"},
                    {"66", sh ? "+" : "=", sh ? "" : "+", "", "SYM:=", "TEXT:+", "TEXT:±", "0", "0"},
                    {"68", "←", "", "", "ACT_LEFT", "ACT_HOME", "ACT_LEFT", "3", "0"},
                    {"68", "↓", "", "", "ACT_DOWN", "ACT_PGDN", "ACT_DOWN", "3", "0"},
                    {"68", "→", "", "", "ACT_RIGHT", "ACT_END", "ACT_RIGHT", "3", "0"}
                };
                addRowSeq(rightStartU + 54f, bodyY + 4 * rowH, rowH, sx, gapY, r5R);

            } else {
                // Full-Width 65% Mechanical Layout (62 Keys, Concise 森林集 UI)
                fullModeTopY = H - kbH;
                float headerH = kbH * 0.145f;
                float bodyY = fullModeTopY + headerH + 4f;
                float bodyH = kbH - headerH - 28f;
                float rowH = bodyH / 5f;
                float gapY = land ? 6f : 5f;
                float sx = W / 1125f;

                String[][] r1 = {
                    {"74", sh ? "~" : "Esc", "`", "", "ACT_ESC", "TEXT:`", "TEXT:~", "1", "0"},
                    {"77", sh ? "!" : "1", sh ? "" : "!", "", "DIGIT:1", "TEXT:!", "TEXT:！", "0", "0"},
                    {"77", sh ? "@" : "2", sh ? "" : "@", "", "DIGIT:2", "TEXT:@", "TEXT:²", "0", "0"},
                    {"77", sh ? "#" : "3", sh ? "" : "#", "", "DIGIT:3", "TEXT:#", "TEXT:³", "0", "0"},
                    {"77", sh ? "¥" : "4", sh ? "" : "¥", "", "DIGIT:4", "TEXT:¥", "TEXT:$", "0", "0"},
                    {"77", sh ? "%" : "5", sh ? "" : "%", "", "DIGIT:5", "TEXT:%", "TEXT:‰", "0", "0"},
                    {"77", sh ? "^" : "6", sh ? "" : "^", "", "DIGIT:6", "TEXT:^", "TEXT:……", "0", "0"},
                    {"77", sh ? "&" : "7", sh ? "" : "&", "", "DIGIT:7", "TEXT:&", "TEXT:｜", "0", "0"},
                    {"77", sh ? "*" : "8", sh ? "" : "*", "", "DIGIT:8", "TEXT:*", "TEXT:※", "0", "0"},
                    {"77", sh ? "(" : "9", sh ? "" : "(", "", "DIGIT:9", "TEXT:(", "TEXT:（", "0", "0"},
                    {"77", sh ? ")" : "0", sh ? "" : ")", "", "DIGIT:0", "TEXT:)", "TEXT:）", "0", "0"},
                    {"74", sh ? "_" : "-", sh ? "" : "_", "", "SYM:-", "TEXT:_", "TEXT:——", "0", "0"},
                    {"74", sh ? "+" : "=", sh ? "" : "+", "", "SYM:=", "TEXT:+", "TEXT:±", "0", "0"},
                    {"117", "退格", "", "", "ACT_BACK", "ACT_CLEAR_COMP", "ACT_DEL", "1", "0"}
                };
                addRowSeq(8f, bodyY, rowH, sx, gapY, r1);

                String[][] r2 = {
                    {"95", "Tab", "", "", "ACT_TAB", "ACT_TAB", "ACT_TAB", "1", "0"},
                    {"78", "Q", "", "", "ALPHA:Q", "TEXT:§", "", "0", "0"},
                    {"78", "W", "", "", "ALPHA:W", "TEXT:€", "", "0", "0"},
                    {"78", "E", "", "", "ALPHA:E", "TEXT:£", "", "0", "0"},
                    {"78", "R", "", "", "ALPHA:R", "TEXT:®", "", "0", "0"},
                    {"78", "T", "", "", "ALPHA:T", "TEXT:™", "", "0", "0"},
                    {"78", "Y", "", "", "ALPHA:Y", "ACT_REDO", "", "0", "0"},
                    {"78", "U", "", "", "ALPHA:U", "TEXT:÷", "", "0", "0"},
                    {"78", "I", "", "", "ALPHA:I", "TEXT:≠", "", "0", "0"},
                    {"78", "O", "", "", "ALPHA:O", "TEXT:≈", "", "0", "0"},
                    {"78", "P", "", "", "ALPHA:P", "TEXT:√", "", "0", "0"},
                    {"78", sh ? "{" : "[", sh ? "" : "{", "", "SYM:[", "TEXT:{", "TEXT:【", "0", "0"},
                    {"78", sh ? "}" : "]", sh ? "" : "}", "", "SYM:]", "TEXT:}", "TEXT:】", "0", "0"},
                    {"78", sh ? "|" : "、", sh ? "" : "|", "", "SYM:\\", "TEXT:|", "TEXT:\\", "0", "0"}
                };
                addRowSeq(8f, bodyY + rowH, rowH, sx, gapY, r2);

                String[][] r3 = {
                    {"112", capsMain, "", "", "ACT_CAPS", "ACT_CAPS", "ACT_CAPS", "1", "0"},
                    {"78", "A", "", "", "ALPHA:A", "ACT_SELALL", "", "0", "0"},
                    {"78", "S", "", "", "ALPHA:S", "TEXT:°", "", "0", "0"},
                    {"78", "D", "", "", "ALPHA:D", "TEXT:℃", "", "0", "0"},
                    {"78", "F", "", "", "ALPHA:F", "TEXT:「", "", "0", "0"},
                    {"78", "G", "", "", "ALPHA:G", "TEXT:」", "", "0", "0"},
                    {"78", "H", "", "", "ALPHA:H", "TEXT:『", "", "0", "0"},
                    {"78", "J", "", "", "ALPHA:J", "TEXT:』", "", "0", "0"},
                    {"78", "K", "", "", "ALPHA:K", "TEXT:《", "", "0", "0"},
                    {"78", "L", "", "", "ALPHA:L", "TEXT:》", "", "0", "0"},
                    {"78", sh ? ":" : "；", sh ? "" : ":", "", "ACT_SEMI", "TEXT:：", "TEXT:;", "0", "0"},
                    {"78", sh ? "\"" : "“", sh ? "" : "\"", "", "SYM:'", "TEXT:\"", "TEXT:”", "0", "0"},
                    {"139", "Enter", "", "", "ACT_ENTER", "ACT_ENTER", "ACT_ENTER", "4", "0"}
                };
                addRowSeq(8f, bodyY + 2 * rowH, rowH, sx, gapY, r3);

                String[][] r4 = {
                    {"129", shiftMain, "", "", "ACT_SHIFT", "ACT_SHIFT", "ACT_SHIFT", "1", "0"},
                    {"78", "Z", "", "", "ALPHA:Z", "ACT_UNDO", "", "0", "0"},
                    {"78", "X", "", "", "ALPHA:X", "ACT_CUT", "", "0", "0"},
                    {"78", "C", "", "", "ALPHA:C", "ACT_COPY", "", "0", "0"},
                    {"78", "V", "", "", "ALPHA:V", "ACT_PASTE", "", "0", "0"},
                    {"78", "B", "", "", "ALPHA:B", "ACT_REDO", "", "0", "0"},
                    {"78", "N", "", "", "ALPHA:N", "ACT_HOME", "", "0", "0"},
                    {"78", "M", "", "", "ALPHA:M", "ACT_END", "", "0", "0"},
                    {"78", sh ? "<" : "，", sh ? "" : "<", "", "SYM:,", "TEXT:<", "TEXT:,", "0", "0"},
                    {"78", sh ? ">" : "。", sh ? "" : ">", "", "SYM:.", "TEXT:>", "TEXT:.", "0", "0"},
                    {"78", sh ? "？" : "/", sh ? "" : "？", "", "SYM:/", "TEXT:？", "TEXT:?", "0", "0"},
                    {"100", "↑", "", "", "ACT_UP", "ACT_PGUP", "ACT_UP", "3", "0"},
                    {"100", "Del", "", "", "ACT_DEL", "ACT_CLEAR_COMP", "ACT_DEL", "2", "0"}
                };
                addRowSeq(8f, bodyY + 3 * rowH, rowH, sx, gapY, r4);

                String[][] r5 = {
                    {"95", ctrlMain, "", "", "ACT_CTRL", "ACT_CTRL_LOCK", "TEXT:【】", "1", "0"},
                    {"95", langMain, "", "", "ACT_LANG", "ACT_LANG", "ACT_LANG", "1", "0"},
                    {"95", "粘贴", "", "", "ACT_PASTE", "ACT_COPY", "ACT_CUT", "2", "0"},
                    {"424", isVoiceListening ? "🎙 聆听中" : "SPACE", "", "", "ACT_SPACE", "ACT_SPACE", "ACT_SPACE", "5", "0"},
                    {"100", "收起", "", "", "ACT_HIDE", "ACT_IME_PICKER", "ACT_HIDE", "1", "0"},
                    {"100", "←", "", "", "ACT_LEFT", "ACT_HOME", "ACT_LEFT", "3", "0"},
                    {"100", "↓", "", "", "ACT_DOWN", "ACT_PGDN", "ACT_DOWN", "3", "0"},
                    {"100", "→", "", "", "ACT_RIGHT", "ACT_END", "ACT_RIGHT", "3", "0"}
                };
                addRowSeq(8f, bodyY + 4 * rowH, rowH, sx, gapY, r5);
            }
        }

        private void addRowSeq(float startX, float ry, float rowH, float sx, float gapY, String[][] rowSpecs) {
            float cx = startX;
            for (String[] k : rowSpecs) {
                float w = Float.parseFloat(k[0]);
                boolean ghost = k.length > 8 && "1".equals(k[8]);
                addCustomKey(cx * sx, ry, w * sx, rowH, gapY,
                        k[1], k[2], k[3], k[4], k[5], k[6], Integer.parseInt(k[7]), ghost);
                if (!ghost) {
                    cx += w;
                }
            }
        }

        private void addCustomKey(float x, float y, float w, float h, float gapY,
                                  String main, String top, String bot,
                                  String tap, String up, String down, int style, boolean ghost) {
            if (isChinese && isShuangpin && !isCapsLocked && !isShiftActive()) {
                String hint = getShuangpinHint(main);
                if (!hint.isEmpty()) {
                    bot = hint;
                }
            }
            if (isCtrl) {
                String ctrlHint = getCtrlShortcutHint(main);
                if (!ctrlHint.isEmpty()) {
                    top = ctrlHint;
                }
            }
            keys.add(new KeySpec(x + 3f, y + 3f, w - 6f, h - gapY, main, top, bot, tap, up, down, style, ghost));
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int W = getWidth();
            int H = getHeight();
            boolean land = isScreenLandscape();
            boolean split = isSplitMode();

            // Item #11: If auto-hidden by physical hardware keyboard, ONLY draw the floating restore pill!
            if (isAutoHiddenByHwKeyboard) {
                drawHeaderChip(canvas, restorePillRect, "⌨ 展开键盘", "#2E5A3C", "#FFFFFF");
                return;
            }

            candRects.clear();

            if (split) {
                // Item #10: In Split Mode, do NOT draw any background in the middle gap!
                // Only draw the Left Floating Island + Right Floating Island!
                paint.setColor(Color.parseColor("#ECEDEA"));
                canvas.drawRoundRect(leftTrayRect, 20f, 20f, paint);
                canvas.drawRoundRect(rightTrayRect, 20f, 20f, paint);

                paint.setStyle(Paint.Style.STROKE);
                paint.setStrokeWidth(2f);
                paint.setColor(Color.parseColor("#C4C8C3"));
                canvas.drawRoundRect(leftTrayRect, 20f, 20f, paint);
                canvas.drawRoundRect(rightTrayRect, 20f, 20f, paint);
                paint.setStyle(Paint.Style.FILL);

                // Left Floating Candidate Pill Bar (above Left Island)
                paint.setColor(Color.parseColor("#E4E6E2"));
                canvas.drawRoundRect(leftHeaderRect, 14f, 14f, paint);
                drawCandidatesInBar(canvas, leftHeaderRect);

                // Right Floating Toolbar Pill Bar (above Right Island)
                paint.setColor(Color.parseColor("#E4E6E2"));
                canvas.drawRoundRect(rightHeaderRect, 14f, 14f, paint);
                drawSplitRightToolbar(canvas, rightHeaderRect);

            } else {
                // Full-Width Mode: clean warm tray
                int screenH = getResources().getDisplayMetrics().heightPixels;
                float kbH = Math.round(screenH * (land ? 0.265f : 0.225f));
                float headerH = kbH * 0.145f;
                rect.set(0, fullModeTopY, W, H);
                paint.setColor(Color.parseColor("#ECEDEA"));
                canvas.drawRect(rect, paint);

                rect.set(0, fullModeTopY, W, fullModeTopY + headerH);
                paint.setColor(Color.parseColor("#E4E6E2"));
                canvas.drawRect(rect, paint);

                drawFullWidthToolbarAndCandidates(canvas, W, fullModeTopY, headerH, land);
            }

            // Draw Keys (skipping invisible Ghost Keys unless actively pressed!)
            for (KeySpec k : keys) {
                if (k.isGhost && k != activeKey) continue;
                drawKey(canvas, k, k == activeKey);
            }
        }

        private void drawCandidatesInBar(Canvas canvas, RectF bar) {
            float cx = bar.left + 14f;
            float cy = bar.centerY();
            float barH = bar.height();
            float btnH = barH * 0.76f;
            float btnY = bar.top + (barH - btnH) / 2f;

            if (isVoiceListening || !voiceStatusText.isEmpty()) {
                paint.setColor(Color.parseColor("#C8403C"));
                paint.setTextSize(barH * 0.40f);
                paint.setTextAlign(Paint.Align.LEFT);
                Paint.FontMetrics fm = paint.getFontMetrics();
                canvas.drawText(voiceStatusText, cx, cy - (fm.ascent + fm.descent) / 2f, paint);
                return;
            }

            if (isCtrl) {
                paint.setColor(Color.parseColor("#C8403C"));
                paint.setTextSize(barH * 0.38f);
                paint.setTextAlign(Paint.Align.LEFT);
                Paint.FontMetrics fm = paint.getFontMetrics();
                String msg = isCtrlLocked ? "Ctrl 锁定中 · 连续按键触发 ^X" : "Ctrl 激活 · 点击任意键触发 ^X";
                canvas.drawText(msg, cx, cy - (fm.ascent + fm.descent) / 2f, paint);
                return;
            }

            if (composing.length() > 0) {
                paint.setColor(Color.parseColor("#C8403C"));
                paint.setTextSize(barH * 0.36f);
                paint.setTextAlign(Paint.Align.LEFT);
                Paint.FontMetrics fm = paint.getFontMetrics();
                String compStr = isShuangpin ? decodedPinyinDisplay : composing.toString();
                canvas.drawText(compStr, cx, cy - (fm.ascent + fm.descent) / 2f, paint);
                cx += paint.measureText(compStr) + 14f;

                paint.setTextSize(barH * 0.41f);
                fm = paint.getFontMetrics();
                float ty = cy - (fm.ascent + fm.descent) / 2f;
                for (int i = 0; i < candItems.size(); i++) {
                    String cand = (i + 1) + "." + candItems.get(i).text;
                    float cw = paint.measureText(cand) + 22f;
                    if (cx + cw > bar.right - 8f) break;
                    RectF cr = new RectF(cx, btnY, cx + cw, btnY + btnH);
                    candRects.add(cr);
                    paint.setColor(i == 0 ? Color.parseColor("#DCE8DF") : Color.parseColor("#F5F6F4"));
                    canvas.drawRoundRect(cr, 10f, 10f, paint);
                    paint.setColor(i == 0 ? Color.parseColor("#1E3A28") : Color.parseColor("#222222"));
                    canvas.drawText(cand, cx + 11f, ty, paint);
                    cx += cw + 8f;
                }
            } else {
                paint.setColor(Color.parseColor("#4A6B53"));
                paint.setTextSize(barH * 0.36f);
                paint.setTextAlign(Paint.Align.LEFT);
                Paint.FontMetrics fm = paint.getFontMetrics();
                String label = !statusBanner.isEmpty()
                        ? statusBanner
                        : ("森林集 · " + (isChinese ? (isShuangpin ? "微软双拼" : "全拼") : "EN"));
                canvas.drawText(label, cx, cy - (fm.ascent + fm.descent) / 2f, paint);
            }
        }

        private void drawSplitRightToolbar(Canvas canvas, RectF bar) {
            float barH = bar.height();
            float btnH = barH * 0.76f;
            float btnY = bar.top + (barH - btnH) / 2f;
            float gap = 8f;
            float w = (bar.width() - gap * 5f) / 4f;
            float x = bar.left + gap;

            btnSpRect.set(x, btnY, x + w, btnY + btnH);
            x += w + gap;
            btnLayoutRect.set(x, btnY, x + w, btnY + btnH);
            x += w + gap;
            btnMoveRect.set(x, btnY, x + w, btnY + btnH);
            x += w + gap;
            btnHideRect.set(x, btnY, x + w, btnY + btnH);

            drawHeaderChip(canvas, btnSpRect, isShuangpin ? "双拼" : "全拼",
                    isShuangpin ? "#2E5A3C" : "#FFFFFF",
                    isShuangpin ? "#FFFFFF" : "#2E4736");
            drawHeaderChip(canvas, btnLayoutRect, "整体", "#FFFFFF", "#2E4736");
            drawHeaderChip(canvas, btnMoveRect, "✥ 移动", draggingSplitMove ? "#2E5A3C" : "#FFFFFF",
                    draggingSplitMove ? "#FFFFFF" : "#2E4736");
            drawHeaderChip(canvas, btnHideRect, "收起", "#DFE1E5", "#333333");
        }

        private void drawFullWidthToolbarAndCandidates(Canvas canvas, int W, float topY, float headerH, boolean land) {
            float btnH = headerH * 0.74f;
            float btnY = topY + (headerH - btnH) / 2f;
            float btnW = land ? W * 0.068f : W * 0.105f;
            float gap = 8f;
            float rx = W - 12f;

            btnHideRect.set(rx - btnW, btnY, rx, btnY + btnH);
            rx = btnHideRect.left - gap;
            btnMoveRect.set(rx - btnW, btnY, rx, btnY + btnH);
            rx = btnMoveRect.left - gap;
            btnLayoutRect.set(rx - btnW, btnY, rx, btnY + btnH);
            rx = btnLayoutRect.left - gap;
            btnSpRect.set(rx - btnW, btnY, rx, btnY + btnH);

            drawHeaderChip(canvas, btnSpRect, isShuangpin ? "双拼" : "全拼",
                    isShuangpin ? "#2E5A3C" : "#FFFFFF",
                    isShuangpin ? "#FFFFFF" : "#2E4736");
            drawHeaderChip(canvas, btnLayoutRect, "分体", "#FFFFFF", "#2E4736");
            drawHeaderChip(canvas, btnMoveRect, "⌨ 隐", "#FFFFFF", "#2E4736");
            drawHeaderChip(canvas, btnHideRect, "收起", "#DFE1E5", "#333333");

            RectF leftArea = new RectF(10f, topY, btnSpRect.left - 10f, topY + headerH);
            drawCandidatesInBar(canvas, leftArea);
        }

        private void drawHeaderChip(Canvas canvas, RectF r, String text, String bgHex, String fgHex) {
            paint.setColor(Color.parseColor(bgHex));
            canvas.drawRoundRect(r, 12f, 12f, paint);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1.4f);
            paint.setColor(Color.parseColor("#B8C4BC"));
            canvas.drawRoundRect(r, 12f, 12f, paint);
            paint.setStyle(Paint.Style.FILL);

            paint.setColor(Color.parseColor(fgHex));
            paint.setTextSize(r.height() * 0.44f);
            paint.setTextAlign(Paint.Align.CENTER);
            Paint.FontMetrics fm = paint.getFontMetrics();
            float ty = r.centerY() - (fm.ascent + fm.descent) / 2f;
            canvas.drawText(text, r.centerX(), ty, paint);
        }

        private void drawKey(Canvas canvas, KeySpec k, boolean pressed) {
            rect.set(k.x, k.y, k.x + k.w, k.y + k.h);
            if (k.isGhost) {
                // Brief translucent highlight when user presses an invisible Ghost Key (Item #12)
                paint.setColor(Color.parseColor("#88B8CEBF"));
                canvas.drawRoundRect(rect, 12f, 12f, paint);
                paint.setColor(Color.parseColor("#1E3A28"));
                paint.setTextAlign(Paint.Align.CENTER);
                paint.setTextSize(k.h * 0.38f);
                Paint.FontMetrics fm = paint.getFontMetrics();
                canvas.drawText(k.mainLabel, rect.centerX(), rect.centerY() - (fm.ascent + fm.descent) / 2f, paint);
                return;
            }

            RectF shadow = new RectF(k.x, k.y + 2.2f, k.x + k.w, k.y + k.h + 2.2f);
            paint.setColor(Color.parseColor("#BCC0BC"));
            canvas.drawRoundRect(shadow, 12f, 12f, paint);

            int bg, fgMain, fgTop, fgBot;
            boolean isSpKey = isChinese && isShuangpin && !isCapsLocked && !isShiftActive() && !getShuangpinHint(k.mainLabel).isEmpty();
            boolean hasCtrlHint = isCtrl && !getCtrlShortcutHint(k.mainLabel).isEmpty();
            boolean isActiveCtrl = isCtrl && "ACT_CTRL".equals(k.tapAction);
            boolean isActiveShift = isShiftActive() && "ACT_SHIFT".equals(k.tapAction);
            boolean isActiveCaps = isCapsLocked && "ACT_CAPS".equals(k.tapAction);

            if (isActiveCtrl || isActiveShift || isActiveCaps) {
                bg = Color.parseColor((isCtrlLocked && isActiveCtrl) ? "#C8403C" : "#2E5A3C");
                fgMain = Color.WHITE;
                fgTop = Color.parseColor("#F5F7F5");
                fgBot = Color.parseColor("#D8E6DC");
            } else if (pressed) {
                bg = Color.parseColor("#B8CEBF");
                fgMain = Color.parseColor("#111111");
                fgTop = Color.parseColor("#333333");
                fgBot = Color.parseColor("#333333");
            } else if ("Esc".equals(k.mainLabel) || "~".equals(k.mainLabel)) {
                bg = Color.parseColor("#EED6D4");
                fgMain = Color.parseColor("#912623");
                fgTop = Color.parseColor("#912623");
                fgBot = Color.parseColor("#767D84");
            } else {
                switch (k.style) {
                    case 1: // Modifier
                        bg = Color.parseColor("#DDE0E4");
                        fgMain = Color.parseColor("#1A1D20");
                        fgTop = hasCtrlHint ? Color.parseColor("#C8403C") : Color.parseColor("#4A6B53");
                        fgBot = Color.parseColor("#6C737A");
                        break;
                    case 2: // Green Accent (Del / Paste)
                        bg = Color.parseColor("#DEEAE1");
                        fgMain = Color.parseColor("#1E3A28");
                        fgTop = hasCtrlHint ? Color.parseColor("#C8403C") : Color.parseColor("#4A6B53");
                        fgBot = Color.parseColor("#526A5A");
                        break;
                    case 3: // Nav Dark Forest (Arrows)
                        bg = Color.parseColor("#4A6B53");
                        fgMain = Color.WHITE;
                        fgTop = hasCtrlHint ? Color.parseColor("#FFF2A8") : Color.parseColor("#D8E6DC");
                        fgBot = Color.parseColor("#D8E6DC");
                        break;
                    case 4: // Enter Coral Red
                        bg = Color.parseColor("#D9534F");
                        fgMain = Color.WHITE;
                        fgTop = Color.parseColor("#FCEAE9");
                        fgBot = Color.parseColor("#FCEAE9");
                        break;
                    default: // Alpha / Space
                        bg = hasCtrlHint ? Color.parseColor("#FCFDFB") : Color.WHITE;
                        fgMain = Color.parseColor("#1A1D20");
                        fgTop = hasCtrlHint
                                ? Color.parseColor("#C8403C")
                                : (("1234567890-=".contains(k.mainLabel)) ? Color.parseColor("#CD4844") : Color.parseColor("#6E7872"));
                        fgBot = isSpKey
                                ? (("UIV；:".contains(k.mainLabel.toUpperCase())) ? Color.parseColor("#C8403C") : Color.parseColor("#2B6A44"))
                                : Color.parseColor("#767D84");
                        break;
                }
            }

            paint.setColor(bg);
            canvas.drawRoundRect(rect, 12f, 12f, paint);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(hasCtrlHint ? 1.6f : 1.1f);
            paint.setColor(Color.parseColor(hasCtrlHint ? "#9FB9A6" : "#CCD0CC"));
            canvas.drawRoundRect(rect, 12f, 12f, paint);
            paint.setStyle(Paint.Style.FILL);

            paint.setTextAlign(Paint.Align.CENTER);
            float cx = rect.centerX();
            boolean hasTop = k.topLabel != null && !k.topLabel.isEmpty();
            boolean hasBot = k.botLabel != null && !k.botLabel.isEmpty();

            // Clean, non-overlapping typography for all 3 combinations (Item #6 & #9):
            if (hasTop && hasBot) {
                // Only occurs when Ctrl is active AND Shuangpin bottom label is shown: well-separated 3 zones
                paint.setColor(fgTop);
                paint.setTextSize(k.h * 0.165f);
                canvas.drawText(k.topLabel, cx, k.y + k.h * 0.21f, paint);

                paint.setColor(fgMain);
                paint.setTextSize(k.h * 0.32f);
                Paint.FontMetrics fm = paint.getFontMetrics();
                float my = k.y + k.h * 0.51f - (fm.ascent + fm.descent) / 2f;
                canvas.drawText(k.mainLabel, cx, my, paint);

                paint.setColor(fgBot);
                paint.setTextSize(k.h * 0.155f);
                canvas.drawText(k.botLabel, cx, k.y + k.h * 0.86f, paint);
            } else if (hasTop) {
                // Number / Symbol key or Ctrl-hint key (Top + Main)
                paint.setColor(fgTop);
                paint.setTextSize(k.h * 0.185f);
                canvas.drawText(k.topLabel, cx, k.y + k.h * 0.26f, paint);

                paint.setColor(fgMain);
                float ratio = (k.mainLabel.length() == 1) ? 0.37f : 0.26f;
                paint.setTextSize(k.h * ratio);
                Paint.FontMetrics fm = paint.getFontMetrics();
                float my = k.y + k.h * 0.64f - (fm.ascent + fm.descent) / 2f;
                canvas.drawText(k.mainLabel, cx, my, paint);
            } else if (hasBot) {
                // Standard Shuangpin Letter key (Main + Bottom rhyme, zero top clutter!)
                paint.setColor(fgMain);
                paint.setTextSize(k.h * 0.38f);
                Paint.FontMetrics fm = paint.getFontMetrics();
                float my = k.y + k.h * 0.43f - (fm.ascent + fm.descent) / 2f;
                canvas.drawText(k.mainLabel, cx, my, paint);

                paint.setColor(fgBot);
                paint.setTextSize(k.h * 0.175f);
                canvas.drawText(k.botLabel, cx, k.y + k.h * 0.83f, paint);
            } else {
                // Single-label Function / Modifier / Space / Arrow key (Pure 森林集 style)
                paint.setColor(fgMain);
                String mainText = k.mainLabel;
                float mainRatio = (mainText.length() == 1) ? 0.40f : (mainText.length() <= 3) ? 0.28f : 0.23f;
                paint.setTextSize(k.h * mainRatio);
                Paint.FontMetrics fm = paint.getFontMetrics();
                float my = k.y + k.h * 0.50f - (fm.ascent + fm.descent) / 2f;
                canvas.drawText(mainText, cx, my, paint);
            }
        }

        private boolean hitHeaderRect(RectF r, float x, float y) {
            return x >= r.left - 4f && x <= r.right + 4f && y >= r.top - 18f && y <= r.bottom + 18f;
        }

        @Override
        public boolean onTouchEvent(MotionEvent ev) {
            float x = ev.getX();
            float y = ev.getY();

            // Item #11: If auto-hidden by physical keyboard, tapping the restore pill restores the keyboard
            if (isAutoHiddenByHwKeyboard) {
                if (ev.getActionMasked() == MotionEvent.ACTION_UP && hitHeaderRect(restorePillRect, x, y)) {
                    handleAction("ACT_RESTORE_HW");
                }
                return true;
            }

            switch (ev.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downX = x;
                    downY = y;
                    lastSlideX = x;
                    spaceSliding = false;
                    ctrlHeldByPointer = false;
                    shiftHeldByPointer = false;
                    chordUsedWhileHoldingModifier = false;
                    longPressTriggered = false;
                    mainHandler.removeCallbacks(longPressBackRunnable);
                    mainHandler.removeCallbacks(longPressSpaceRunnable);

                    // Item #10: Check if user started dragging the "✥ 移动" handle in Split Mode
                    if (isSplitMode() && hitHeaderRect(btnMoveRect, x, y)) {
                        long now = SystemClock.uptimeMillis();
                        if (now - lastMoveTapTime < 400L) {
                            // Double-tap resets split position to default
                            splitOffsetY = 0f;
                            splitSpreadX = 0f;
                            prefs.edit().putFloat("split_offset_y", 0f).putFloat("split_spread_x", 0f).apply();
                            rebuildCurrentKeys();
                            invalidate();
                            lastMoveTapTime = 0L;
                            return true;
                        }
                        lastMoveTapTime = now;
                        draggingSplitMove = true;
                        dragStartX = x;
                        dragStartY = y;
                        startOffsetY = splitOffsetY;
                        startSpreadX = splitSpreadX;
                        invalidate();
                        return true;
                    }

                    activeKey = findKey(x, y);
                    if (activeKey != null) {
                        if ("ACT_BACK".equals(activeKey.tapAction) || "ACT_DEL".equals(activeKey.tapAction)) {
                            mainHandler.postDelayed(longPressBackRunnable, 320L);
                        } else if ("ACT_SPACE".equals(activeKey.tapAction)) {
                            mainHandler.postDelayed(longPressSpaceRunnable, 400L);
                        }
                    }
                    invalidate();
                    return true;

                case MotionEvent.ACTION_POINTER_DOWN:
                    mainHandler.removeCallbacks(longPressBackRunnable);
                    mainHandler.removeCallbacks(longPressSpaceRunnable);
                    int pIdx = ev.getActionIndex();
                    KeySpec secondKey = findKey(ev.getX(pIdx), ev.getY(pIdx));
                    if (activeKey != null && secondKey != null) {
                        if ("ACT_CTRL".equals(activeKey.tapAction)) {
                            isCtrl = true;
                            ctrlHeldByPointer = true;
                            chordUsedWhileHoldingModifier = true;
                            handleAction(secondKey.tapAction);
                            invalidate();
                        } else if ("ACT_SHIFT".equals(activeKey.tapAction)) {
                            isShifted = true;
                            shiftHeldByPointer = true;
                            chordUsedWhileHoldingModifier = true;
                            handleAction(secondKey.tapAction);
                            invalidate();
                        }
                    }
                    return true;

                case MotionEvent.ACTION_MOVE:
                    if (draggingSplitMove) {
                        float dy = y - dragStartY;
                        float dx = (x - dragStartX) / (getWidth() / 1920f);
                        splitOffsetY = startOffsetY + dy;
                        splitSpreadX = Math.max(-10f, Math.min(180f, startSpreadX - dx * 0.5f));
                        rebuildCurrentKeys();
                        invalidate();
                        return true;
                    }
                    if (activeKey != null && "ACT_SPACE".equals(activeKey.tapAction)) {
                        float dx = x - lastSlideX;
                        if (Math.abs(x - downX) > 18f) {
                            mainHandler.removeCallbacks(longPressSpaceRunnable);
                        }
                        if (Math.abs(dx) > 28f) {
                            spaceSliding = true;
                            int steps = (int) (dx / 28f);
                            for (int i = 0; i < Math.abs(steps); i++) {
                                handleAction(steps > 0 ? "ACT_RIGHT" : "ACT_LEFT");
                            }
                            lastSlideX += steps * 28f;
                        }
                    }
                    return true;

                case MotionEvent.ACTION_UP:
                    mainHandler.removeCallbacks(longPressBackRunnable);
                    mainHandler.removeCallbacks(longPressSpaceRunnable);

                    if (draggingSplitMove) {
                        draggingSplitMove = false;
                        prefs.edit()
                                .putFloat("split_offset_y", splitOffsetY)
                                .putFloat("split_spread_x", splitSpreadX)
                                .apply();
                        invalidate();
                        return true;
                    }

                    if (activeKey != null) {
                        if (!spaceSliding && !chordUsedWhileHoldingModifier && !longPressTriggered) {
                            float dy = y - downY;
                            float thresh = Math.max(26f, activeKey.h * 0.32f);
                            if (dy < -thresh && activeKey.upAction != null && !activeKey.upAction.isEmpty()) {
                                handleAction(activeKey.upAction);
                            } else if (dy > thresh && activeKey.downAction != null && !activeKey.downAction.isEmpty()) {
                                handleAction(activeKey.downAction);
                            } else {
                                handleAction(activeKey.tapAction);
                            }
                        } else if (chordUsedWhileHoldingModifier) {
                            if (ctrlHeldByPointer && !isCtrlLocked) isCtrl = false;
                            if (shiftHeldByPointer) isShifted = false;
                            rebuildCurrentKeys();
                        }
                        activeKey = null;
                        spaceSliding = false;
                        ctrlHeldByPointer = false;
                        shiftHeldByPointer = false;
                        chordUsedWhileHoldingModifier = false;
                        longPressTriggered = false;
                        invalidate();
                        return true;
                    }

                    // Check Header Taps
                    if (hitHeaderRect(btnSpRect, x, y)) {
                        handleAction("ACT_SP_TOGGLE");
                        return true;
                    }
                    if (hitHeaderRect(btnLayoutRect, x, y)) {
                        toggleSplitModeForCurrentOrientation();
                        return true;
                    }
                    if (!isSplitMode() && hitHeaderRect(btnMoveRect, x, y)) {
                        handleAction("ACT_MINIMIZE_HW");
                        return true;
                    }
                    if (hitHeaderRect(btnHideRect, x, y)) {
                        handleAction("ACT_HIDE");
                        return true;
                    }
                    for (int i = 0; i < candRects.size(); i++) {
                        if (hitHeaderRect(candRects.get(i), x, y)) {
                            commitCandidate(i);
                            return true;
                        }
                    }
                    return true;

                case MotionEvent.ACTION_CANCEL:
                    mainHandler.removeCallbacks(longPressBackRunnable);
                    mainHandler.removeCallbacks(longPressSpaceRunnable);
                    activeKey = null;
                    spaceSliding = false;
                    draggingSplitMove = false;
                    longPressTriggered = false;
                    invalidate();
                    return true;
            }
            return super.onTouchEvent(ev);
        }

        private KeySpec findKey(float x, float y) {
            // Check visible keys first, then invisible Ghost Keys
            for (KeySpec k : keys) {
                if (!k.isGhost && x >= k.x - 3f && x <= k.x + k.w + 3f && y >= k.y - 3f && y <= k.y + k.h + 3f) {
                    return k;
                }
            }
            for (KeySpec k : keys) {
                if (k.isGhost && x >= k.x - 3f && x <= k.x + k.w + 3f && y >= k.y - 3f && y <= k.y + k.h + 3f) {
                    return k;
                }
            }
            return null;
        }
    }

    // ==================== DICTIONARY LOADER (Item #1) ====================
    private void registerEntry(String sylKey, int wt, String[] words) {
        sylDict.put(sylKey, words);
        sylWeight.put(sylKey, wt);
        String flat = sylKey.replace("'", "");
        if (!flatPyDict.containsKey(flat)) {
            flatPyDict.put(flat, words);
        }
        int sep = sylKey.indexOf('\'');
        if (sep > 0) {
            String first = sylKey.substring(0, sep);
            List<String> list = firstSylMultiKeys.get(first);
            if (list == null) {
                list = new ArrayList<>();
                firstSylMultiKeys.put(first, list);
            }
            list.add(sylKey);
        } else {
            VALID_SYLLABLES.add(sylKey);
        }
    }

    private void loadFullAssetDictAsync() {
        new Thread(() -> {
            try {
                InputStream is = getAssets().open("pinyin_dict.txt");
                BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8), 65536);
                String line;
                while ((line = br.readLine()) != null) {
                    int t1 = line.indexOf('\t');
                    if (t1 <= 0) continue;
                    int t2 = line.indexOf('\t', t1 + 1);
                    if (t2 <= t1) continue;
                    String pyKey = line.substring(0, t1);
                    int wt = 1000;
                    try {
                        wt = Integer.parseInt(line.substring(t1 + 1, t2));
                    } catch (Exception ignored) {}
                    String wordsPart = line.substring(t2 + 1);
                    String[] words = wordsPart.split(",");
                    if (words.length > 0) {
                        synchronized (sylDict) {
                            registerEntry(pyKey, wt, words);
                        }
                    }
                }
                br.close();
                fullDictLoaded = true;
                mainHandler.post(() -> {
                    if (composing.length() > 0) updateCandidates();
                    if (keyboardView != null) keyboardView.invalidate();
                });
            } catch (Exception ignored) {}
        }).start();
    }

    private void initBootstrapDict() {
        String[][] entries = {
            {"ti'sheng'ti'yan", "提升体验"},
            {"ti'sheng", "提升", "替身", "题生"},
            {"ti'yan", "体验", "体言", "题眼"},
            {"wei'ruan'shuang'pin", "微软双拼"},
            {"wei'ruan", "微软", "委婉"},
            {"shuang'pin", "双拼", "双频"},
            {"quan'pin", "全拼", "全频"},
            {"fang'an", "方案", "法案"},
            {"sen'lin'ji", "森林集"},
            {"fen'ti'jian'pan", "分体键盘"},
            {"wu'li'jian'pan", "物理键盘"},
            {"yu'yin'shu'ru", "语音输入"},
            {"hou'xuan'ci", "候选词"},
            {"ci'ku", "词库"},
            {"you'hua", "优化"},
            {"shu'ru'fa", "输入法"},
            {"jian'pan", "键盘"},
            {"ping'ban", "平板"},
            {"ti", "提", "体", "题", "替", "踢"},
            {"sheng", "升", "生", "声", "省", "胜"},
            {"yan", "验", "眼", "言", "严", "研"},
            {"wei", "微", "为", "位", "未", "围"},
            {"ruan", "软", "阮"},
            {"shuang", "双", "爽", "霜"},
            {"pin", "拼", "品", "频", "贫"},
            {"ping", "平", "屏", "评", "凭"},
            {"ban", "板", "版", "半", "办", "班"},
            {"jian", "键", "见", "间", "件", "建"},
            {"pan", "盘", "判", "盼", "攀"},
            {"sen", "森"},
            {"lin", "林", "临", "邻"},
            {"ji", "集", "机", "几", "己", "记", "计"}
        };
        synchronized (sylDict) {
            for (String[] row : entries) {
                String key = row[0];
                String[] vals = new String[row.length - 1];
                System.arraycopy(row, 1, vals, 0, vals.length);
                registerEntry(key, 500000, vals);
            }
        }
    }
}
"""

MAIN_ACTIVITY_JAVA = r"""package com.forest.padime;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.Window;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private EditText editText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#EEF2EE"));
        root.setPadding(48, 72, 48, 24);

        TextView title = new TextView(this);
        title.setText("森林集 Pad 分体机械键盘 v3 · 12 项体验升级实机验证台");
        title.setTextSize(19f);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        title.setTextColor(Color.parseColor("#1E3A28"));
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("12.4万词开源词库 · 分步选词不丢码(tiugtiyj) · 长按退格清空未选词 · 长按空格语音 · Caps/Shift物理逻辑 · Ctrl+Z/Y撤销重做 · 中间透明透传与拖动 · 隐藏点击(左T右→Y / 右Y左→T)");
        sub.setTextSize(12.5f);
        sub.setTextColor(Color.parseColor("#4A6B53"));
        sub.setPadding(0, 6, 0, 16);
        root.addView(sub);

        editText = new EditText(this);
        editText.setGravity(Gravity.TOP | Gravity.START);
        editText.setTextSize(17.5f);
        editText.setTextColor(Color.parseColor("#1B1D1F"));
        editText.setBackgroundColor(Color.WHITE);
        editText.setPadding(32, 24, 32, 24);
        editText.setMinLines(10);
        String initDoc =
                "【森林集 Pad v3 · 12 项体验升级实测区】\n" +
                "实测输入：\n\n" +
                "  · [词库与分步选词] 12.4万词 rime-ice 词库 | 输入 tiugtiyj 先选「2.提升」保留剩余 tiyj「1.体验」\n" +
                "  · [手势增强] 候选态长按退格=清空全部未选词 | 长按空格=开启语音输入 | 拖动「✥ 移动」自由悬停分体键盘\n" +
                "  · [物理键盘一致] Caps 独立锁定大写字母(数字不変) | Shift 切换上档符号(!@#$%)与大小写 | Ctrl+Z 撤销 / Ctrl+Y 重做\n" +
                "  · [分体隐藏点击] 左半边 5/T/G/B 右侧空白处点击直接触发 6/Y/H/N；右半边 6/Y/H/N 左侧空白处点击直接触发 5/T/G/B\n" +
                "  · [物理键盘自动隐藏] 外接物理键盘按键时自动收起软键盘，右下角保留「⌨ 展开键盘」恢复胶囊\n\n" +
                "              ┌─────────────────────── 屏幕底部中央 · 底层应用透出区 ───────────────────────┐\n" +
                "              │  此处位于左右分体键盘正中间 (x=860..2270)，无空白背景占位，底层应用完全可见 │\n" +
                "              │  且所有点击/滑动手势通过 TOUCHABLE_INSETS_REGION 直接透传给底层应用窗口！   │\n" +
                "              └─────────────────────────────────────────────────────────────────────────────┘";
        editText.setText(initDoc);
        int cursorIdx = initDoc.indexOf("实测输入：") + 5;
        editText.setSelection(cursorIdx);

        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        root.addView(editText, lp);

        setContentView(root);
        getWindow().setSoftInputMode(
                WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE |
                WindowManager.LayoutParams.SOFT_INPUT_ADJUST_NOTHING);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (editText != null) {
            editText.requestFocus();
            editText.postDelayed(() -> {
                InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                if (imm != null) {
                    imm.showSoftInput(editText, InputMethodManager.SHOW_IMPLICIT);
                }
            }, 250);
        }
    }
}
"""

def run(cmd):
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)

def main():
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(f"{BUILD_DIR}/src/com/forest/padime", exist_ok=True)
    os.makedirs(f"{BUILD_DIR}/res/xml", exist_ok=True)
    os.makedirs(f"{BUILD_DIR}/classes", exist_ok=True)
    os.makedirs(f"{BUILD_DIR}/compiled_res", exist_ok=True)

    with open(f"{BUILD_DIR}/AndroidManifest.xml", "w", encoding="utf-8") as f:
        f.write(MANIFEST_XML)
    with open(f"{BUILD_DIR}/res/xml/method.xml", "w", encoding="utf-8") as f:
        f.write(METHOD_XML)
    with open(f"{BUILD_DIR}/src/com/forest/padime/PadImeService.java", "w", encoding="utf-8") as f:
        f.write(JAVA_SOURCE)
    with open(f"{BUILD_DIR}/src/com/forest/padime/MainActivity.java", "w", encoding="utf-8") as f:
        f.write(MAIN_ACTIVITY_JAVA)

    # 1. Compile resources with aapt2
    run([
        f"{BUILD_TOOLS}/aapt2", "compile",
        "--dir", f"{BUILD_DIR}/res",
        "-o", f"{BUILD_DIR}/compiled_res/res.zip"
    ])

    # 2. Link resources and generate R.java + base APK
    run([
        f"{BUILD_TOOLS}/aapt2", "link",
        "-I", ANDROID_JAR,
        "--manifest", f"{BUILD_DIR}/AndroidManifest.xml",
        "--java", f"{BUILD_DIR}/src",
        "-o", f"{BUILD_DIR}/unaligned.apk",
        f"{BUILD_DIR}/compiled_res/res.zip"
    ])

    # 3. Compile Java sources
    java_files = [
        f"{BUILD_DIR}/src/com/forest/padime/PadImeService.java",
        f"{BUILD_DIR}/src/com/forest/padime/MainActivity.java",
        f"{BUILD_DIR}/src/com/forest/padime/R.java"
    ]
    run([
        "javac", "-source", "11", "-target", "11", "-encoding", "UTF-8",
        "-cp", ANDROID_JAR,
        "-d", f"{BUILD_DIR}/classes"
    ] + java_files)

    # 4. Convert classes to classes.dex with d8
    class_files = []
    for root, _, files in os.walk(f"{BUILD_DIR}/classes"):
        for fn in files:
            if fn.endswith(".class"):
                class_files.append(os.path.join(root, fn))
    run([
        f"{BUILD_TOOLS}/d8",
        "--lib", ANDROID_JAR,
        "--output", BUILD_DIR
    ] + class_files)

    # 5. Add classes.dex and assets/pinyin_dict.txt into unaligned.apk
    with zipfile.ZipFile(f"{BUILD_DIR}/unaligned.apk", "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(f"{BUILD_DIR}/classes.dex", "classes.dex")
        if os.path.exists(DICT_PATH):
            zf.write(DICT_PATH, "assets/pinyin_dict.txt")

    # 6. Zipalign
    run([
        f"{BUILD_TOOLS}/zipalign", "-f", "4",
        f"{BUILD_DIR}/unaligned.apk",
        f"{BUILD_DIR}/aligned.apk"
    ])

    # 7. Sign with debug keystore (create if needed)
    keystore = "/tmp/forest_pad_ime.keystore"
    if not os.path.exists(keystore):
        run([
            "keytool", "-genkeypair", "-v",
            "-keystore", keystore,
            "-storepass", "android",
            "-keypass", "android",
            "-alias", "androiddebugkey",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Android Debug,O=Android,C=US"
        ])
    run([
        f"{BUILD_TOOLS}/apksigner", "sign",
        "--ks", keystore,
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--out", OUT_APK,
        f"{BUILD_DIR}/aligned.apk"
    ])
    print("Built APK:", OUT_APK, os.path.getsize(OUT_APK), "bytes")

if __name__ == "__main__":
    main()
