import Foundation
import Speech
import AVFoundation

/// Push-to-talk speech-to-text using Apple's on-device Speech framework
/// (SFSpeechRecognizer). No audio leaves the phone for transcription --
/// only the final transcribed text gets sent to your PC's server.
///
/// Usage: call `start()` when the mic button is pressed, `stop()` when
/// released (or after a pause). `transcript` updates live; read its final
/// value in `stop()`'s completion.
@MainActor
final class SpeechRecognizer: ObservableObject {
    @Published var transcript: String = ""
    @Published var isRecording: Bool = false
    @Published var errorMessage: String?

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var audioEngine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    func requestPermissions(completion: @escaping (Bool) -> Void) {
        SFSpeechRecognizer.requestAuthorization { authStatus in
            AVAudioSession.sharedInstance().requestRecordPermission { granted in
                DispatchQueue.main.async {
                    completion(authStatus == .authorized && granted)
                }
            }
        }
    }

    func start() {
        guard let recognizer, recognizer.isAvailable else {
            errorMessage = "Speech recognition isn't available right now."
            return
        }

        // Clean up any previous session first.
        stop()
        transcript = ""
        errorMessage = nil

        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            errorMessage = "Audio session error: \(error.localizedDescription)"
            return
        }

        let engine = AVAudioEngine()
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        // Prefer fully on-device recognition when the platform supports it.
        if #available(iOS 13, *) {
            req.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        }

        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            req.append(buffer)
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            errorMessage = "Couldn't start the microphone: \(error.localizedDescription)"
            return
        }

        self.audioEngine = engine
        self.request = req
        self.isRecording = true

        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                if let result {
                    self.transcript = result.bestTranscription.formattedString
                }
                if error != nil || (result?.isFinal ?? false) {
                    self.stopEngine()
                }
            }
        }
    }

    /// Stops recording. The final transcript is left in `transcript` for
    /// the caller to read and send.
    func stop() {
        stopEngine()
    }

    private func stopEngine() {
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine?.stop()
        request?.endAudio()
        task?.cancel()

        audioEngine = nil
        request = nil
        task = nil
        isRecording = false

        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
