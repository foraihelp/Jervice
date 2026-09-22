import SwiftUI

struct ContentView: View {
    @StateObject private var settings = Settings.shared
    @StateObject private var speechRecognizer = SpeechRecognizer()
    @State private var messages: [ChatMessage] = []
    @State private var isSending = false
    @State private var showSettings = false
    private let speaker = Speaker()
    private let network = NetworkClient()

    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                if !settings.isConfigured {
                    Banner(text: "Set up your PC's address in Settings before talking to Jarvis.")
                }

                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            ForEach(messages) { message in
                                MessageBubble(message: message)
                                    .id(message.id)
                            }
                        }
                        .padding()
                    }
                    .onChange(of: messages) { _ in
                        if let last = messages.last {
                            withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }

                if !speechRecognizer.transcript.isEmpty && speechRecognizer.isRecording {
                    Text(speechRecognizer.transcript)
                        .font(.footnote)
                        .foregroundColor(.secondary)
                        .padding(.horizontal)
                        .padding(.bottom, 4)
                }

                pushToTalkButton
                    .padding(.bottom, 24)
            }
            .navigationTitle("Jarvis")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .onAppear {
                speechRecognizer.requestPermissions { granted in
                    if !granted {
                        messages.append(ChatMessage(sender: .system, text: "Microphone/speech permission was denied. Enable it in iOS Settings > Jarvis to use push-to-talk."))
                    }
                }
            }
        }
    }

    private var pushToTalkButton: some View {
        Button {
            // no-op; using press gesture below for push-to-talk semantics
        } label: {
            Image(systemName: speechRecognizer.isRecording ? "mic.fill" : "mic")
                .font(.system(size: 32))
                .foregroundColor(.white)
                .frame(width: 84, height: 84)
                .background(speechRecognizer.isRecording ? Color.red : Color.blue)
                .clipShape(Circle())
                .scaleEffect(speechRecognizer.isRecording ? 1.08 : 1.0)
                .animation(.easeInOut(duration: 0.15), value: speechRecognizer.isRecording)
        }
        .disabled(!settings.isConfigured || isSending)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !speechRecognizer.isRecording && !isSending {
                        speaker.stop()
                        speechRecognizer.start()
                    }
                }
                .onEnded { _ in
                    guard speechRecognizer.isRecording else { return }
                    speechRecognizer.stop()
                    let text = speechRecognizer.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !text.isEmpty {
                        send(text)
                    }
                }
        )
    }

    private func send(_ text: String) {
        messages.append(ChatMessage(sender: .user, text: text))
        isSending = true

        Task {
            do {
                let reply = try await network.sendChat(text: text, settings: settings)
                await MainActor.run {
                    messages.append(ChatMessage(sender: .jarvis, text: reply))
                    isSending = false
                    speaker.speak(reply)
                }
            } catch {
                await MainActor.run {
                    let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                    messages.append(ChatMessage(sender: .system, text: message))
                    isSending = false
                }
            }
        }
    }
}

private struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.sender == .user { Spacer(minLength: 40) }

            Text(message.text)
                .padding(10)
                .background(background)
                .foregroundColor(message.sender == .user ? .white : .primary)
                .cornerRadius(14)

            if message.sender != .user { Spacer(minLength: 40) }
        }
    }

    private var background: Color {
        switch message.sender {
        case .user: return .blue
        case .jarvis: return Color(.secondarySystemBackground)
        case .system: return Color.orange.opacity(0.25)
        }
    }
}

private struct Banner: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.footnote)
            .padding(8)
            .frame(maxWidth: .infinity)
            .background(Color.orange.opacity(0.2))
    }
}

#Preview {
    ContentView()
}
