import Foundation
import AVFoundation

/// Speaks Jarvis's replies using iOS's built-in on-device text-to-speech.
/// No audio/text is sent anywhere for this step -- it's the same engine
/// Siri and VoiceOver are built on.
final class Speaker {
    private let synthesizer = AVSpeechSynthesizer()

    func speak(_ text: String) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, options: [])
        try? session.setActive(true)

        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synthesizer.speak(utterance)
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }
}
