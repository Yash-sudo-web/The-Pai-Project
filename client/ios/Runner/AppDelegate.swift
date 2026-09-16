import Flutter
import AVFAudio
import UIKit
import UserNotifications

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var voiceChannel: FlutterMethodChannel?
  private var processingTask: UIBackgroundTaskIdentifier = .invalid

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    // Route notification callbacks through UNUserNotificationCenter so
    // flutter_local_notifications can present alerts while the app is in the
    // foreground. FlutterAppDelegate already conforms to the delegate
    // protocol; without this line iOS delivers nothing until the app is
    // backgrounded.
    if #available(iOS 10.0, *) {
      UNUserNotificationCenter.current().delegate = self as? UNUserNotificationCenterDelegate
    }
    NotificationCenter.default.addObserver(
      self,
      selector: #selector(audioInterrupted(_:)),
      name: AVAudioSession.interruptionNotification,
      object: nil
    )
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: "pai/locked_voice",
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    voiceChannel = channel
    channel.setMethodCallHandler { [weak self] call, result in
      self?.handleVoiceCall(call, result: result)
    }
  }

  private func handleVoiceCall(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "begin":
      do {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
          .playAndRecord,
          mode: .default,
          options: [.defaultToSpeaker, .allowBluetooth]
        )
        try session.setActive(true)
      } catch {
        result(FlutterError(code: "audio_session", message: error.localizedDescription, details: nil))
        return
      }
      if #available(iOS 16.1, *) {
        Task { @MainActor in
          await PaiVoiceActivityManager.shared.begin()
          result(nil)
        }
      } else {
        result(nil)
      }

    case "beginProcessing":
      if processingTask == .invalid {
        processingTask = UIApplication.shared.beginBackgroundTask(
          withName: "Finish Pai voice turn"
        ) { [weak self] in
          self?.endProcessingTask()
          if #available(iOS 16.1, *) {
            Task { @MainActor in
              await PaiVoiceActivityManager.shared.end()
            }
          }
        }
      }
      result(nil)

    case "endProcessing":
      endProcessingTask()
      result(nil)

    case "phase":
      guard let phase = call.arguments as? String else {
        result(FlutterError(code: "phase", message: "Missing voice phase", details: nil))
        return
      }
      if #available(iOS 16.1, *) {
        Task { @MainActor in
          await PaiVoiceActivityManager.shared.update(phase)
          result(nil)
        }
      } else {
        result(nil)
      }

    case "end":
      endProcessingTask()
      if #available(iOS 16.1, *) {
        Task { @MainActor in
          await PaiVoiceActivityManager.shared.end()
          self.deactivateVoiceSession()
          result(nil)
        }
      } else {
        deactivateVoiceSession()
        result(nil)
      }

    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func endProcessingTask() {
    guard processingTask != .invalid else { return }
    UIApplication.shared.endBackgroundTask(processingTask)
    processingTask = .invalid
  }

  private func deactivateVoiceSession() {
    do {
      try AVAudioSession.sharedInstance().setActive(
        false,
        options: .notifyOthersOnDeactivation
      )
    } catch {
      NSLog("Pai audio session could not deactivate: %@", error.localizedDescription)
    }
  }

  @objc private func audioInterrupted(_ notification: Notification) {
    guard let typeValue = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
          let type = AVAudioSession.InterruptionType(rawValue: typeValue),
          type == .began else { return }
    if #available(iOS 16.1, *) {
      Task { @MainActor in
        await PaiVoiceActivityManager.shared.update("interrupted")
      }
    }
  }
}
