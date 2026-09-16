import ActivityKit
import Foundation
import UIKit

@available(iOS 16.1, *)
@MainActor
final class PaiVoiceActivityManager {
  static let shared = PaiVoiceActivityManager()

  private var activity: Activity<PaiVoiceAttributes>?
  private var turnActive = false

  private init() {}

  func begin() async {
    // A previous turn may have been interrupted before cleanup completed.
    await end()
    turnActive = true
    guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
    do {
      let attributes = PaiVoiceAttributes(startedAt: Date())
      let state = PaiVoiceAttributes.ContentState(phase: "listening")
      if #available(iOS 16.2, *) {
        activity = try Activity.request(
          attributes: attributes,
          content: ActivityContent(
            state: state,
            staleDate: Date().addingTimeInterval(90)
          ),
          pushType: nil
        )
      } else {
        activity = try Activity.request(
          attributes: attributes,
          contentState: state,
          pushType: nil
        )
      }
    } catch {
      NSLog("Pai Live Activity could not start: %@", String(describing: error))
    }
  }

  func update(_ phase: String) async {
    if turnActive && activity == nil && UIApplication.shared.applicationState == .active {
      await begin()
    }
    guard let activity else { return }
    let state = PaiVoiceAttributes.ContentState(phase: phase)
    if #available(iOS 16.2, *) {
      let staleAfter: TimeInterval = phase == "speaking" ? 300 : 90
      await activity.update(ActivityContent(
        state: state,
        staleDate: Date().addingTimeInterval(staleAfter)
      ))
    } else {
      await activity.update(using: state)
    }
  }

  func end() async {
    turnActive = false
    self.activity = nil
    // Also clear an activity left by a previous process after a crash.
    for existing in Activity<PaiVoiceAttributes>.activities {
      if #available(iOS 16.2, *) {
        await existing.end(nil, dismissalPolicy: .immediate)
      } else {
        await existing.end(using: nil, dismissalPolicy: .immediate)
      }
    }
  }
}
