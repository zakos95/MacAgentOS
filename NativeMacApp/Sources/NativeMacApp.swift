import AppKit
import SwiftUI
import Vision
import UniformTypeIdentifiers
import ImageIO
import Foundation

private enum UIPalette {
    static let background = Color(red: 15/255, green: 17/255, blue: 21/255)      // #0F1115
    static let surface = Color(red: 22/255, green: 26/255, blue: 34/255)         // #161A22
    static let hover = Color(red: 29/255, green: 35/255, blue: 48/255)           // #1D2330
    static let border = Color.white.opacity(0.06)

    static let textPrimary = Color(red: 243/255, green: 244/255, blue: 246/255)  // #F3F4F6
    static let textSecondary = Color(red: 156/255, green: 163/255, blue: 175/255) // #9CA3AF

    static let accent = Color(red: 124/255, green: 143/255, blue: 168/255)       // #7C8FA8
    static let success = Color(red: 115/255, green: 169/255, blue: 182/255)
    static let warning = Color(red: 191/255, green: 154/255, blue: 98/255)
    static let error = Color(red: 168/255, green: 116/255, blue: 120/255)
}

@main
struct NativeMacApp: App {
    @NSApplicationDelegateAdaptor(AppLifecycleDelegate.self) private var appDelegate
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup("Mac Agent OS") {
            RootView(appState: appState)
                .frame(minWidth: 1120, minHeight: 760)
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                    appState.terminateBackend()
                }
                .task {
                    await appState.bootstrap()
                    NSApp.activate(ignoringOtherApps: true)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                        if let window = NSApp.windows.first {
                            window.center()
                            window.makeKeyAndOrderFront(nil)
                        }
                    }
                }
        }
        .defaultSize(width: 1280, height: 820)
        .commands {
            CommandGroup(replacing: .appTermination) {
                Button("Quitter Mac Agent OS") {
                    appState.terminateBackend()
                    NSApp.terminate(nil)
                }
                .keyboardShortcut("q")
            }
        }

        Settings {
            SettingsView(appState: appState)
                .frame(width: 620, height: 540)
        }
    }
}

final class AppLifecycleDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            sender.windows.first?.makeKeyAndOrderFront(nil)
        }
        return true
    }
}

@MainActor
@Observable
final class AppState {
    struct RuntimeConfig: Decodable {
        let environment: String
        let backendBaseURL: String
        let healthRetries: Int
        let healthRetryDelayMs: Int
        let defaultTurbo: Bool
        let openAfterBuild: Bool

        static let `default` = RuntimeConfig(
            environment: "dev",
            backendBaseURL: "http://127.0.0.1:8000",
            healthRetries: 2,
            healthRetryDelayMs: 350,
            defaultTurbo: false,
            openAfterBuild: false
        )

        static func load() -> RuntimeConfig {
            let candidates = [
                Bundle.main.url(forResource: "runtime-config", withExtension: "json"),
                URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appending(path: "Config/dev.json")
            ]
            for candidate in candidates.compactMap({ $0 }) {
                if let data = try? Data(contentsOf: candidate),
                   let decoded = try? JSONDecoder().decode(RuntimeConfig.self, from: data) {
                    return decoded
                }
            }
            return .default
        }
    }

    enum SidebarSelection: Hashable {
        case conversation(UUID)
        case localModels
        case skills
        case selfUpdate
        case diagnostics
        case logs
        case settings
    }

    enum ProviderSetupMode: String, CaseIterable, Identifiable {
        case apiKey
        case bridge
        case ollama

        var id: String { rawValue }
    }

    enum AppLanguage: String, CaseIterable, Identifiable {
        case french = "fr"
        case english = "en"

        var id: String { rawValue }

        var label: String {
            switch self {
            case .french: return "Français"
            case .english: return "English"
            }
        }
    }

    struct ChatMessage: Codable, Identifiable, Equatable {
        enum Role: String, Codable {
            case user
            case assistant
            case system
        }

        struct LocalActionRequest: Codable, Equatable {
            let type: String
            let payload: [String: String]
            let steps: [LocalActionRequest]?
        }

        struct LocalActionApproval: Codable, Equatable {
            enum Status: String, Codable {
                case pending
                case running
                case completed
                case cancelled
                case failed
            }

            let objective: String
            let plan: [String]
            let actionTitle: String
            let request: LocalActionRequest
            var status: Status
            var resultText: String
        }

        let id: UUID
        let role: Role
        let text: String
        let meta: String
        let executionInfo: String?
        var localActionApproval: LocalActionApproval?
        let createdAt: Date

        init(
            id: UUID = UUID(),
            role: Role,
            text: String,
            meta: String,
            executionInfo: String? = nil,
            localActionApproval: LocalActionApproval? = nil,
            createdAt: Date = .now
        ) {
            self.id = id
            self.role = role
            self.text = text
            self.meta = meta
            self.executionInfo = executionInfo
            self.localActionApproval = localActionApproval
            self.createdAt = createdAt
        }
    }

    struct Conversation: Codable, Identifiable, Equatable {
        let id: UUID
        var title: String
        let createdAt: Date
        var updatedAt: Date
        var messages: [ChatMessage]

        init(
            id: UUID = UUID(),
            title: String,
            createdAt: Date = .now,
            updatedAt: Date = .now,
            messages: [ChatMessage] = []
        ) {
            self.id = id
            self.title = title
            self.createdAt = createdAt
            self.updatedAt = updatedAt
            self.messages = messages
        }
    }

    struct SettingsPayload: Decodable {
        let provider: String
        let model: String
        let base_url: String
        // Optional split-model config — nil means "use primary provider/model"
        let chat_provider: String?
        let chat_model: String?
        let chat_base_url: String?
        let planner_provider: String?
        let planner_model: String?
        let planner_base_url: String?
    }

    struct HealthPayload: Decodable {
        let ready: Bool
    }

    struct ChatReply: Decodable {
        struct ProviderModelInfo: Decodable {
            let provider: String
            let model: String
        }

        struct RouteInfo: Decodable {
            let tier: String?
            let reason: String?
            let fallback_reason: String?
        }

        let type: String
        let content: String
        let provider: String?
        let model: String?
        let requested: ProviderModelInfo?
        let actual: ProviderModelInfo?
        let route: RouteInfo?
    }

    struct LocalActionPlanReply: Decodable {
        struct ActionPayload: Decodable {
            let app_name: String?
            let target_path: String?
            let content: String?
            let instruction: String?
            let url: String?
            let source_path: String?
            let output_path: String?
        }

        struct Action: Decodable {
            let type: String
            let payload: ActionPayload
            let steps: [Action]?
        }

        let type: String
        let objective: String?
        let plan: [String]?
        let action: Action?
        let error: String?
    }

    struct LocalActionExecuteReply: Decodable {
        struct StepResult: Decodable {
            let index: Int
            let label: String
            let status: String
            let result: String
        }

        let status: String
        let result: String?
        let error: String?
        let steps: [StepResult]?
    }

    struct ChatGPTStatusPayload: Decodable {
        struct OAuthInfo: Decodable {
            let connected: Bool
            let expired: Bool?
            let account_id: String?
        }

        struct BridgeInfo: Decodable {
            let installed: Bool
            let connected: Bool
            let expired: Bool?
            let status: String
            let error: String
            let login_hint: String?
        }

        let running: Bool
        let status: String
        let error: String
        let connected: Bool
        let oauth: OAuthInfo?
        let bridge: BridgeInfo?
    }

    struct ModelsPayload: Decodable {
        let provider: String
        let models: [String]
    }

    struct LocalModelsPayload: Decodable {
        let models: [String]
    }

    struct APIErrorPayload: Decodable {
        let detail: String?
        let error: String?
    }

    struct ProviderModelsPayload: Decodable {
        let provider: String
        let models: [String]
    }

    struct ProviderConnectionDescriptor: Decodable, Identifiable {
        struct RuntimeStatus: Decodable {
            let provider: String?
            let installed: Bool?
            let connected: Bool?
            let expired: Bool?
            let status: String?
            let error: String?
            let login_hint: String?
        }

        let id: String
        let label: String
        let auth_mode: String
        let enabled: Bool
        let supports_api_key: Bool
        let supports_base_url: Bool
        let supports_model_listing: Bool
        let supports_connection_test: Bool
        let message: String
        let runtime: RuntimeStatus?
    }

    struct ProviderConnectionsPayload: Decodable {
        let providers: [ProviderConnectionDescriptor]
    }

    struct SkillDescriptor: Decodable, Identifiable, Equatable {
        let id: String
        let name: String
        let description: String
        let category: String
        let allowed_tools: [String]
        let triggers: [String]
        let enabled: Bool
        let risk: String
        let examples: [String]
        let available: Bool
        let availability_message: String
    }

    struct SkillsPayload: Decodable {
        let skills: [SkillDescriptor]
    }

    struct SkillMutationPayload: Decodable {
        let skill: SkillDescriptor
    }

    struct SkillTestPayload: Decodable {
        let id: String
        let status: String
        let message: String
    }

    struct ProviderConnection: Codable, Identifiable, Equatable {
        let id: String
        var label: String
        var authMode: String
        var enabled: Bool
        var supportsAPIKey: Bool
        var supportsBaseURL: Bool
        var supportsModelListing: Bool
        var supportsConnectionTest: Bool
        var message: String
        var apiKey: String
        var baseURL: String
        var model: String
        var availableModels: [String]
        var statusText: String
        var errorText: String

        var resolvedBaseURL: String {
            switch id {
            case "ollama":
                return baseURL.isEmpty ? "http://localhost:11434" : baseURL
            case "openai":
                return baseURL.isEmpty ? "https://api.openai.com/v1" : baseURL
            case "openai_compatible":
                return baseURL
            default:
                return baseURL
            }
        }
    }

    struct DiagnosticsPayload: Decodable {
        struct SettingsInfo: Decodable {
            let provider: String
            let model: String
            let base_url: String
        }

        struct ChatGPTInfo: Decodable {
            let connected: Bool
            let expired: Bool?
        }

        struct BridgeInfo: Decodable {
            let installed: Bool
            let connected: Bool
            let expired: Bool?
            let status: String?
            let error: String?
        }

        struct HereticInfo: Decodable {
            let installed: Bool
            let version: String
        }

        struct OllamaInfo: Decodable {
            let available: Bool
            let base_url: String
            let model_count: Int
            let message: String
        }

        struct MCPInfo: Decodable {
            struct SkippedServer: Decodable {
                let name: String
                let reason: String
            }

            let active: [String]
            let active_count: Int
            let skipped: [SkippedServer]
            let skipped_count: Int
        }

        let ready: Bool
        let settings: SettingsInfo
        let chatgpt: ChatGPTInfo
        let bridge: BridgeInfo?
        let heretic: HereticInfo?
        let ollama: OllamaInfo?
        let mcp: MCPInfo?
        let local_model_count: Int
        let local_models_preview: [String]
        let log_entries: [String]
    }

    struct SelfUpdateCheck: Decodable, Identifiable, Equatable {
        var id: String { command }
        let command: String
        let returncode: Int?
        let ok: Bool?
        let output: String?
    }

    struct SelfUpdateValidation: Decodable, Equatable {
        let status: String?
        let message: String?
        let working_path: String?
        let checks: [SelfUpdateCheck]?
    }

    struct SelfUpdateBuildInfo: Decodable, Equatable {
        let status: String?
        let message: String?
        let candidate_app: String?
    }

    struct SelfUpdateStep: Decodable, Identifiable, Equatable {
        var id: String { "\(name)-\(path ?? "")-\(message ?? "")" }
        let name: String
        let status: String?
        let message: String?
        let path: String?
    }

    struct SelfUpdateResult: Decodable, Equatable {
        let status: String?
        let message: String?
        let root: String?
        let safe_path: String?
        let working_path: String?
        let safe_exists: Bool?
        let working_exists: Bool?
        let candidate_app: String?
        let target_app: String?
        let backup_app: String?
        let required_confirmation: String?
        let proposal_path: String?
        let ai_response: String?
        let provider: String?
        let model: String?
        let context_files: [String]?
        let suggestions: [String]?
        let validation: SelfUpdateValidation?
        let build: SelfUpdateBuildInfo?
        let diagnosis: SelfUpdateBuildInfo?
        let steps: [SelfUpdateStep]?
    }

    struct ComposerAttachment: Identifiable, Equatable {
        enum Kind: String {
            case text = "Texte"
            case image = "Image"
            case document = "Document"
        }

        let id: UUID
        let url: URL
        let name: String
        let kind: Kind
        let payload: String

        init(id: UUID = UUID(), url: URL, name: String, kind: Kind, payload: String) {
            self.id = id
            self.url = url
            self.name = name
            self.kind = kind
            self.payload = payload
        }
    }

    var sidebarSelection: SidebarSelection?
    var conversations: [Conversation] = []
    var currentConversationID: UUID?
    var inputText: String = ""
    var currentModel: String = "Chargement..."
    var provider: String = "openai"
    var statusText: String = "Connexion au backend..."
    var appLanguage: AppLanguage = AppLanguage(rawValue: UserDefaults.standard.string(forKey: "MacAgentOS.language") ?? "") ?? .french {
        didSet {
            UserDefaults.standard.set(appLanguage.rawValue, forKey: "MacAgentOS.language")
            refreshLocalizedRuntimeText()
        }
    }
    var isBackendReady = false
    var isSending = false
    var reasoningEnabled = true
    var turboEnabled = false
    var chatGPTConnected = false
    var chatGPTAccountLabel = "Compte non connecté"
    var chatGPTStatusText = "Session ChatGPT Bridge indisponible"
    var openAIModels: [String] = []
    var localExperimentalModels: [String] = []
    var composerAttachments: [ComposerAttachment] = []
    var activeProjectPath: String = ""
    var availableProviders: [String] = ["openai", "ollama"]
    var availableModels: [String] = []
    var providerConnections: [ProviderConnection] = []
    var skills: [SkillDescriptor] = []
    var skillsStatusText: String = ""
    var testingSkillIDs: Set<String> = []
    var selfUpdateStatus: SelfUpdateResult?
    var selfUpdateLastResult: SelfUpdateResult?
    var selfUpdateStatusText: String = ""
    var selfUpdateWorkingPath: String = "\(NSHomeDirectory())/Desktop/MacAgentOS-SelfUpdate/MacAgentOS-WORKING"
    var selfUpdateOutputRoot: String = "\(NSHomeDirectory())/Desktop/MacAgentOS-SelfUpdate/candidate-app"
    var selfUpdateCandidateApp: String = "\(NSHomeDirectory())/Desktop/MacAgentOS-SelfUpdate/candidate-app/Mac Agent OS.app"
    var selfUpdateTargetApp: String = "\(NSHomeDirectory())/Desktop/Mac agent os V1.1/Mac Agent OS.app"
    var selfUpdateBackupRoot: String = "\(NSHomeDirectory())/Desktop/MacAgentOS-SelfUpdate/backups"
    var selfUpdateConfirmation: String = ""
    var selfUpdateRollbackBackupApp: String = ""
    var selfUpdateObjective: String = "Analyse les logs, le diagnostic et le code self-update, puis propose l'amélioration minimale suivante pour rendre l'app plus autonome et stable."
    var isSelfUpdateRunning = false
    var settingsStatusText: String = ""
    var selectedProviderMode: ProviderSetupMode = .apiKey
    var selectedAPIProviderID: String = "openai"
    var logs: [String] = []
    var logAnalysis: String = ""
    var isAnalyzingLogs = false
    var backendLogEntries: [String] = []
    var diagnosticsSummary: DiagnosticsPayload?
    var liveActivityTitle: String = ""
    var liveActivityDetails: [String] = []
    var shouldForceScrollOnNextAssistantMessage = false
    var userIsReviewingHistory = false
    var pendingScrollMessageID: UUID?
    let runtimeConfig = RuntimeConfig.load()

    // ── Bearer auth ──────────────────────────────────────────────────────────
    var bearerToken: String = ""
    var bearerTokenError: String = ""

    func l(_ french: String, _ english: String) -> String {
        appLanguage == .english ? english : french
    }

    private func refreshLocalizedRuntimeText() {
        if isBackendReady {
            statusText = l("Backend prêt", "Backend ready")
        } else if statusText == "Connexion au backend..." || statusText == "Connecting to backend..." {
            statusText = l("Connexion au backend...", "Connecting to backend...")
        } else if statusText == "Backend non prêt" || statusText == "Backend not ready" {
            statusText = l("Backend non prêt", "Backend not ready")
        }
    }

    var localizedStatusText: String {
        switch statusText {
        case "Connexion au backend...": return l("Connexion au backend...", "Connecting to backend...")
        case "Backend prêt": return l("Backend prêt", "Backend ready")
        case "Backend non prêt": return l("Backend non prêt", "Backend not ready")
        case "Démarrage du backend...": return l("Démarrage du backend...", "Starting backend...")
        case "Backend introuvable": return l("Backend introuvable", "Backend not found")
        case "Port 8000 occupé par un autre service": return l("Port 8000 occupé par un autre service", "Port 8000 is used by another service")
        default: return statusText
        }
    }

    enum RuntimeUserError: LocalizedError {
        case message(String)

        var errorDescription: String? {
            switch self {
            case .message(let message):
                return message
            }
        }
    }

    /// Load the API key written by server.py at startup.
    /// Tries (in order): env var → Application Support (production) → dev paths.
    private func loadBearerToken() {
        // 1. Environment variable override
        if let env = ProcessInfo.processInfo.environment["MACAGENT_API_KEY"], !env.isEmpty {
            bearerToken = env
            bearerTokenError = ""
            return
        }
        // 2. Candidate paths — production path first, then dev fallbacks
        let appSupportKey = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first?.appendingPathComponent("MacAgentOS/data/api_key.txt")

        let cwdParent = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .deletingLastPathComponent()
            .appendingPathComponent("data/api_key.txt")
        let bundleParent = Bundle.main.bundleURL
            .deletingLastPathComponent()  // MacOS
            .deletingLastPathComponent()  // Contents
            .deletingLastPathComponent()  // .app
            .deletingLastPathComponent()  // build output
            .deletingLastPathComponent()  // debug|release
            .deletingLastPathComponent()  // .build
            .deletingLastPathComponent()  // NativeMacApp
            .appendingPathComponent("data/api_key.txt")

        let candidates = [appSupportKey, cwdParent, bundleParent].compactMap { $0 }
        for url in candidates {
            if let raw = try? String(contentsOf: url, encoding: .utf8) {
                let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                if !token.isEmpty {
                    bearerToken = token
                    bearerTokenError = ""
                    return
                }
            }
        }
        bearerTokenError = "Token introuvable — le backend n'a pas encore démarré."
    }

    // ── Backend process management ────────────────────────────────────────────
    private var backendProcess: Process?
    private var backendLogHandle: FileHandle?

    private enum BackendProbeResult {
        case ready(Bool)
        case unavailable
        case occupied(String)
    }

    private func probeBackend() async -> BackendProbeResult {
        do {
            let (data, response) = try await URLSession.shared.data(from: serverBaseURL.appending(path: "/health"))
            if let httpResponse = response as? HTTPURLResponse,
               !(200..<300).contains(httpResponse.statusCode) {
                return .occupied("Port 8000 occupé par un autre service.")
            }
            do {
                let payload = try JSONDecoder().decode(HealthPayload.self, from: data)
                return .ready(payload.ready)
            } catch {
                return .occupied("Port 8000 répond, mais ce n’est pas Mac Agent OS.")
            }
        } catch {
            return .unavailable
        }
    }

    /// Launch the bundled MacAgentServer binary from Resources (if present).
    /// Safe to call multiple times — no-op if the process is already running.
    /// Falls back silently if the binary is not bundled (dev workflow).
    @discardableResult
    private func launchBundledBackendIfPresent() -> Bool {
        // Prevent duplicate launches
        if let existing = backendProcess, existing.isRunning { return true }

        guard let resourcePath = Bundle.main.resourcePath else { return false }
        let binaryPath = (resourcePath as NSString).appendingPathComponent("MacAgentServer")
        guard FileManager.default.isExecutableFile(atPath: binaryPath) else {
            // Not bundled — dev mode, server started externally
            return false
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: binaryPath)
        process.currentDirectoryURL = URL(fileURLWithPath: resourcePath)

        // Merge env: inherit current environment, force production mode
        var env = ProcessInfo.processInfo.environment
        env["MAC_AGENT_ENV"] = "prod"
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env

        // Redirect I/O to a file so the app never blocks on pipes and failures
        // remain diagnosable outside the UI.
        let logsDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/MacAgentOS/data/logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logURL = logsDir.appendingPathComponent("backend-bundle.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd()
            backendLogHandle = handle
            process.standardOutput = handle
            process.standardError = handle
        } else {
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice
        }

        // Clean up reference when process exits
        process.terminationHandler = { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.backendProcess = nil
                self?.backendLogHandle = nil
                self?.appendLog("Backend embarqué terminé (code \(process.terminationStatus)).")
            }
        }

        do {
            try process.run()
            backendProcess = process
            appendLog("Backend embarqué démarré (PID \(process.processIdentifier))")
            return true
        } catch {
            appendLog("Impossible de démarrer le backend : \(error.localizedDescription)")
            return false
        }
    }

    private func ensureBackendAvailable() async -> Bool {
        switch await probeBackend() {
        case .ready(true):
            isBackendReady = true
            statusText = l("Backend prêt", "Backend ready")
            appendLog("Backend existant réutilisé")
            return true
        case .ready(false):
            isBackendReady = false
            statusText = l("Backend non prêt", "Backend not ready")
            appendLog(statusText)
            return false
        case .occupied(let message):
            isBackendReady = false
            statusText = message
            appendLog(message)
            return false
        case .unavailable:
            break
        }

        guard launchBundledBackendIfPresent() else {
            await refreshHealth()
            return isBackendReady
        }

        let attempts = max(runtimeConfig.healthRetries, 40)
        for attempt in 0...attempts {
            switch await probeBackend() {
            case .ready(true):
                isBackendReady = true
                statusText = l("Backend prêt", "Backend ready")
                appendLog(statusText)
                return true
            case .ready(false):
                isBackendReady = false
                statusText = l("Backend non prêt", "Backend not ready")
            case .occupied(let message):
                isBackendReady = false
                statusText = message
                appendLog(message)
                return false
            case .unavailable:
                isBackendReady = false
                statusText = l("Démarrage du backend...", "Starting backend...")
            }

            if attempt < attempts {
                let delay = UInt64(runtimeConfig.healthRetryDelayMs) * 1_000_000
                try? await Task.sleep(nanoseconds: delay)
            }
        }

        statusText = l("Backend embarqué introuvable après démarrage", "Bundled backend not found after startup")
        appendLog(statusText)
        return false
    }

    /// Terminate the backend process when the app quits.
    func terminateBackend() {
        if let process = backendProcess, process.isRunning {
            process.terminate()
            appendLog("Arrêt du backend embarqué demandé")
        }
        backendProcess = nil
        backendLogHandle = nil
    }

    /// Return a URLRequest for `url` with the Bearer token attached (GET by default).
    private func authorized(_ url: URL) -> URLRequest {
        var req = URLRequest(url: url)
        if !bearerToken.isEmpty {
            req.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        return req
    }

    /// Copy `request` and add the Bearer token header.
    private func authorized(_ request: URLRequest) -> URLRequest {
        guard !bearerToken.isEmpty else { return request }
        var req = request
        req.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        return req
    }

    private func dataForAuthorizedRequest(_ request: URLRequest) async throws -> Data {
        var authorizedRequest = authorized(request)
        if authorizedRequest.timeoutInterval <= 0 {
            authorizedRequest.timeoutInterval = 45
        }
        let (data, response) = try await URLSession.shared.data(for: authorizedRequest)
        guard let httpResponse = response as? HTTPURLResponse else {
            return data
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            let payload = try? JSONDecoder().decode(APIErrorPayload.self, from: data)
            let rawMessage = payload?.detail ?? payload?.error ?? HTTPURLResponse.localizedString(forStatusCode: httpResponse.statusCode)
            throw RuntimeUserError.message(userFacingAPIError(rawMessage, statusCode: httpResponse.statusCode))
        }
        return data
    }

    private func dataForAuthorizedURL(_ url: URL) async throws -> Data {
        try await dataForAuthorizedRequest(URLRequest(url: url))
    }

    private func userFacingAPIError(_ message: String, statusCode: Int) -> String {
        if statusCode == 401 || statusCode == 403 || message.localizedCaseInsensitiveContains("api key") {
            return "Clé API locale invalide ou absente. Redémarre le backend puis relance Mac Agent OS pour recharger le token."
        }
        return message
    }

    private func userFacingLLMError(_ message: String, provider: String?) -> String {
        let lower = message.lowercased()
        let providerID = provider?.lowercased() ?? self.provider.lowercased()

        if providerID == "local_chatgpt_codex" {
            return userFacingProviderError(message, provider: "local_chatgpt_codex")
        }
        if providerID == "ollama", lower.contains("11434") || lower.contains("connection refused") {
            return "Ollama ne répond pas sur localhost:11434. Lance Ollama, vérifie qu’un modèle est installé, puis réessaie."
        }
        if lower.contains("provider non support") || lower.contains("unsupported provider") {
            return "Provider indisponible ou non supporté. Ouvre les réglages, choisis un provider actif, puis teste la connexion."
        }
        if lower.contains("invalid api key") || lower.contains("incorrect api key") || lower.contains("401") || lower.contains("403") {
            if providerID == "huggingface" {
                return "Token Hugging Face invalide ou expiré."
            }
            return "Clé API invalide pour ce provider. Vérifie la clé dans les réglages, enregistre, puis teste la connexion."
        }
        if providerID == "huggingface" {
            if lower.contains("insufficient_quota") || lower.contains("quota") || lower.contains("payment required") || lower.contains("402") {
                return "Crédits ou quota Hugging Face insuffisants."
            }
            if lower.contains("model") && (lower.contains("not found") || lower.contains("not available") || lower.contains("unsupported")) {
                return "Ce modèle Hugging Face n’est pas disponible pour l’inférence."
            }
            if lower.contains("timeout") || lower.contains("timed out") || lower.contains("cold start") {
                return "Le modèle met trop de temps à répondre. Réessaie ou choisis un autre modèle."
            }
        }
        return message
    }

    private func describeError(_ error: Error) -> String {
        if let runtimeError = error as? RuntimeUserError,
           let description = runtimeError.errorDescription {
            return description
        }
        if let urlError = error as? URLError, urlError.code == .timedOut {
            return "Le provider actif n’a pas répondu dans le délai. Réessaie, choisis Ollama pour une réponse locale, ou utilise une action locale si tu veux agir sur le Mac."
        }
        return error.localizedDescription
    }

    func userFacingProviderError(_ message: String, provider: String) -> String {
        let lower = message.lowercased()
        if provider == "ollama", lower.contains("connection refused") || lower.contains("11434") {
            return "Ollama n’est pas lancé. Lance Ollama puis réessaie."
        }
        if provider == "local_chatgpt_codex" {
            if lower.contains("not logged in") || lower.contains("aucune session") || lower.contains("no active") {
                return "Aucune session ChatGPT détectée pour le bridge."
            }
            if lower.contains("not installed") || lower.contains("cli non installé") || lower.contains("absent du bundle") || lower.contains("not available") {
                return "Bridge ChatGPT absent du bundle de cette app."
            }
            return "Aucune session ChatGPT détectée pour le bridge."
        }
        if provider == "huggingface" {
            if lower.contains("missing") || lower.contains("required") || lower.contains("absent") || lower.contains("empty token") {
                return "Ajoute un token Hugging Face."
            }
            if lower.contains("api key") || lower.contains("token") || lower.contains("401") || lower.contains("403") || lower.contains("unauthorized") {
                return "Token Hugging Face invalide ou expiré."
            }
            if lower.contains("insufficient_quota") || lower.contains("quota") || lower.contains("payment required") || lower.contains("402") {
                return "Crédits ou quota Hugging Face insuffisants."
            }
            if lower.contains("model") && (lower.contains("not found") || lower.contains("not available") || lower.contains("unsupported")) {
                return "Ce modèle Hugging Face n’est pas disponible pour l’inférence."
            }
            if lower.contains("timeout") || lower.contains("timed out") || lower.contains("cold start") {
                return "Le modèle met trop de temps à répondre. Réessaie ou choisis un autre modèle."
            }
        }
        if lower.contains("api key") || lower.contains("401") || lower.contains("403") || lower.contains("unauthorized") {
            if provider == "huggingface" {
                return "Ajoute un token Hugging Face."
            }
            return "Ajoute une clé API pour utiliser ce provider."
        }
        if lower.contains("aucun modèle") || lower.contains("no model") {
            if provider == "huggingface" {
                return "Ce modèle Hugging Face n’est pas disponible pour l’inférence."
            }
            return "Aucun modèle disponible pour ce provider."
        }
        return message
    }
    // ─────────────────────────────────────────────────────────────────────────

    private var serverBaseURL: URL {
        URL(string: runtimeConfig.backendBaseURL) ?? URL(string: "http://127.0.0.1:8000")!
    }

    var currentConversation: Conversation? {
        guard let id = currentConversationID else { return nil }
        return conversations.first(where: { $0.id == id })
    }

    var currentMessages: [ChatMessage] {
        currentConversation?.messages ?? []
    }

    var currentConversationTitle: String {
        currentConversation?.title ?? "Nouvelle conversation"
    }

    var providerDisplayName: String {
        providerLabel(for: provider)
    }

    var assistantMeta: String {
        if turboEnabled {
            return "\(providerDisplayName) • \(currentModel) • Turbo"
        }
        return reasoningEnabled ? "\(providerDisplayName) • \(currentModel) • \(l("Réflexion", "Reasoning"))" : "\(providerDisplayName) • \(currentModel)"
    }

    var chatHeaderSubtitle: String {
        return "\(providerDisplayName) • \(currentModel)"
    }

    var reasoningLabel: String {
        l("Réflexion", "Reasoning")
    }

    var fastLabel: String {
        l("Rapide", "Fast")
    }

    func bootstrap() async {
        appendLog("Démarrage de Mac Agent OS")
        turboEnabled = runtimeConfig.defaultTurbo
        loadConversations()
        loadProviderConnections()
        ensureActiveConversation()

        // Try early token load (covers already-running server from previous session)
        loadBearerToken()

        // Reuse an existing Mac Agent backend, or start the bundled backend.
        let backendAvailable = await ensureBackendAvailable()

        // Re-load token: if launchBackendIfNeeded() just started the server,
        // the api_key.txt file is now written and can be read.
        if bearerToken.isEmpty {
            loadBearerToken()
        }

        if !bearerTokenError.isEmpty {
            statusText = bearerTokenError
            appendLog("⚠️ Auth: \(bearerTokenError)")
        } else {
            appendLog("Auth: token chargé (\(bearerToken.prefix(6))…)")
        }

        guard backendAvailable else { return }

        await refreshSettings()
        await refreshProviders()
        await refreshChatGPTStatus()
        await refreshModelsForCurrentProvider()
        await refreshLocalModels()
        await refreshDiagnostics()
    }

    func send() async {
        let trimmed = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty || !composerAttachments.isEmpty, !isSending else { return }
        ensureWritableConversation()
        appendLog("Envoi d’un message via \(provider) • \(currentModel)")
        startLiveActivity(reasoningEnabled ? "Réflexion en cours" : "Réponse en cours")
        pushLiveActivity("Analyse de la demande")

        let composedMessage = buildOutgoingMessage(from: trimmed)
        let userVisibleText = buildVisibleUserMessage(from: trimmed)
        let historyPayload = currentMessages.suffix(4).map { message in
            [
                "role": message.role == .assistant ? "assistant" : "user",
                "content": message.text
            ]
        }

        appendMessage(.init(role: .user, text: userVisibleText, meta: "Vous"))
        inputText = ""
        userIsReviewingHistory = false
        shouldForceScrollOnNextAssistantMessage = true
        isSending = true

        defer { isSending = false }

        do {
            // try? — if the planner fails (network error, decode error, auth hiccup)
            // we fall through to normal LLM chat instead of surfacing an error.
            if composerAttachments.isEmpty,
               let planned = try? await planLocalAction(for: trimmed) {
                appendMessage(.init(
                    role: .system,
                    text: "",
                    meta: "Action locale",
                    localActionApproval: planned
                ))
                finishLiveActivity("Validation requise")
                return
            }

            if !composerAttachments.isEmpty {
                pushLiveActivity("Préparation de \(composerAttachments.count) pièce(s) jointe(s)")
            }
            pushLiveActivity("Connexion à \(providerDisplayName)")
            let body = try JSONSerialization.data(withJSONObject: [
                "provider": provider,
                "model": currentModel,
                "message": composedMessage,
                "system_prompt": buildSystemPrompt(),
                "attachments": composerAttachments.map(\.url.path),
                "history": historyPayload,
                "project_path": activeProjectPath,
                "turbo": turboEnabled,
                "reasoning": reasoningEnabled,
                "allow_auto_routing": false
            ])

            var request = URLRequest(url: serverBaseURL.appending(path: "/api/llm/chat"))
            request.timeoutInterval = provider == "local_chatgpt_codex" ? 90 : 60
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")

            pushLiveActivity("Le modèle traite la requête")
            let data = try await dataForAuthorizedRequest(request)
            let decoded = try JSONDecoder().decode(ChatReply.self, from: data)
            if decoded.type == "error" {
                let message = userFacingLLMError(decoded.content, provider: decoded.actual?.provider ?? decoded.provider)
                appendMessage(.init(role: .system, text: message, meta: "Erreur"))
                appendLog("Erreur modèle: \(message)")
                failLiveActivity("Erreur pendant la réponse")
                return
            }
            appendMessage(.init(
                role: .assistant,
                text: decoded.content,
                meta: assistantMeta(for: decoded),
                executionInfo: executionInfo(for: decoded)
            ))
            composerAttachments = []
            appendLog("Réponse reçue du modèle")
            finishLiveActivity("Réponse prête")
        } catch {
            let message = describeError(error)
            appendMessage(.init(role: .system, text: message, meta: "Erreur"))
            appendLog("Erreur d’envoi: \(message)")
            failLiveActivity("Erreur pendant la réponse")
        }
    }

    func approveLocalAction(messageID: UUID) async {
        let currentApproval = localActionApproval(for: messageID)
        updateLocalAction(messageID: messageID) { approval in
            approval.status = .running
            if approval.request.type == "multi_step_plan" {
                let total = approval.request.steps?.count ?? approval.plan.count
                approval.resultText = "Exécution en cours...\nPlan multi-étapes: 0/\(max(total, 1)) terminé."
            } else {
                approval.resultText = "Exécution en cours..."
            }
        }
        startLiveActivity("Exécution locale")
        pushLiveActivity("Autorisation accordée")
        pushLiveActivity("Action locale en cours")

        guard let approval = currentApproval ?? localActionApproval(for: messageID) else {
            failLiveActivity("Action introuvable")
            return
        }

        do {
            var request = URLRequest(url: serverBaseURL.appending(path: "/api/local-actions/execute"))
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "approved": true,
                "action": actionJSONObject(from: approval.request)
            ])

            let data = try await dataForAuthorizedRequest(request)
            let decoded = try JSONDecoder().decode(LocalActionExecuteReply.self, from: data)

            if decoded.status == "success" || decoded.status == "adapted" {
                updateLocalAction(messageID: messageID) { current in
                    current.status = .completed
                    current.resultText = formatLocalActionResult(decoded)
                }
                appendLog("Action locale exécutée: \(approval.actionTitle)")
                finishLiveActivity("Action exécutée")
            } else {
                updateLocalAction(messageID: messageID) { current in
                    current.status = .failed
                    current.resultText = formatLocalActionResult(decoded)
                }
                appendLog("Erreur action locale: \(decoded.error ?? "Erreur inconnue")")
                failLiveActivity("Échec de l’action")
            }
        } catch {
            updateLocalAction(messageID: messageID) { current in
                current.status = .failed
                current.resultText = error.localizedDescription
            }
            appendLog("Erreur action locale: \(error.localizedDescription)")
            failLiveActivity("Échec de l’action")
        }
    }

    func refuseLocalAction(messageID: UUID) {
        updateLocalAction(messageID: messageID) { approval in
            approval.status = .cancelled
            approval.resultText = "Action annulée."
        }
        appendLog("Action locale refusée")
        finishLiveActivity("Action annulée")
    }

    private func actionJSONObject(from request: ChatMessage.LocalActionRequest) -> [String: Any] {
        var object: [String: Any] = [
            "type": request.type,
            "payload": request.payload
        ]
        if let steps = request.steps, !steps.isEmpty {
            object["steps"] = steps.map { actionJSONObject(from: $0) }
        }
        return object
    }

    private func formatLocalActionResult(_ reply: LocalActionExecuteReply) -> String {
        if let steps = reply.steps, !steps.isEmpty {
            let total = steps.count
            let formatted = steps.map { step in
                let marker: String
                switch step.status {
                case "success":
                    marker = "✔️ terminé"
                case "adapted":
                    marker = "↪️ adapté"
                default:
                    marker = "❌ erreur"
                }
                return "Étape \(step.index)/\(total) : \(step.label)\n\(marker)\n\(step.result)"
            }.joined(separator: "\n\n")
            if let result = reply.result, !result.isEmpty {
                return "\(formatted)\n\n\(result)"
            }
            return formatted
        }
        if reply.status == "success" || reply.status == "adapted" {
            return reply.result ?? "Action exécutée."
        }
        return reply.error ?? "Erreur inconnue."
    }

    func importAttachments() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [.image, .plainText, .text, .utf8PlainText, .pdf, .json, .commaSeparatedText]

        guard panel.runModal() == .OK else { return }

        for url in panel.urls {
            if let attachment = makeAttachment(from: url) {
                composerAttachments.append(attachment)
                appendLog("Pièce jointe ajoutée: \(attachment.name)")
            }
        }
    }

    func chooseProjectFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choisir"
        panel.message = "Choisis le dossier projet que Mac Agent OS peut lire, modifier et tester."

        guard panel.runModal() == .OK, let url = panel.urls.first else { return }
        activeProjectPath = url.path
        appendLog("Projet actif: \(url.path)")
    }

    func removeAttachment(_ attachment: ComposerAttachment) {
        composerAttachments.removeAll { $0.id == attachment.id }
    }

    func createConversation() {
        let conversation = Conversation(title: "Nouvelle conversation")
        conversations.insert(conversation, at: 0)
        currentConversationID = conversation.id
        sidebarSelection = .conversation(conversation.id)
        persistConversations()
    }

    func selectConversation(_ id: UUID) {
        currentConversationID = id
        sidebarSelection = .conversation(id)
    }

    func handleSelectionChange(_ selection: SidebarSelection?) {
        guard let selection else { return }
        switch selection {
        case .conversation(let id):
            currentConversationID = id
        case .localModels:
            break
        case .skills:
            Task { await refreshSkills() }
        case .selfUpdate:
            Task { await refreshSelfUpdateStatus() }
        case .diagnostics:
            break
        case .logs:
            break
        case .settings:
            break
        }
    }

    func refreshAll() async {
        await refreshHealth()
        await refreshSettings()
        await refreshProviders()
        await refreshSkills()
        await refreshChatGPTStatus()
        await refreshModelsForCurrentProvider()
        await refreshLocalModels()
        await refreshDiagnostics()
    }

    func connectChatGPT() async {
        do {
            var request = URLRequest(url: serverBaseURL.appending(path: "/api/chatgpt/connect"))
            request.httpMethod = "POST"
            let _ = try await dataForAuthorizedRequest(request)
            await refreshChatGPTStatus()
            await refreshModelsForCurrentProvider()
            appendLog("Connexion ChatGPT Bridge rafraîchie")
        } catch {
            chatGPTStatusText = describeError(error)
            appendLog("Échec de connexion ChatGPT: \(describeError(error))")
        }
    }

    func refreshProviders() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/provider-connections"))
            let payload = try JSONDecoder().decode(ProviderConnectionsPayload.self, from: data)
            mergeProviderConnections(payload.providers)
            availableProviders = providerConnections.filter(\.enabled).map(\.id)
            syncActiveConnectionFromCurrentSettings()
        } catch {
            availableProviders = providerConnections.filter(\.enabled).map(\.id)
            appendLog("Providers indisponibles: \(describeError(error))")
        }
    }

    func refreshSkills() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/skills"))
            let payload = try JSONDecoder().decode(SkillsPayload.self, from: data)
            skills = payload.skills
            skillsStatusText = "Skills chargés"
        } catch {
            skillsStatusText = "Skills indisponibles: \(describeError(error))"
            appendLog(skillsStatusText)
        }
    }

    func setSkillEnabled(_ skillID: String, enabled: Bool) async {
        do {
            var request = URLRequest(url: serverBaseURL.appending(path: "/api/skills/\(skillID)/\(enabled ? "enable" : "disable")"))
            request.httpMethod = "POST"
            let data = try await dataForAuthorizedRequest(request)
            let payload = try JSONDecoder().decode(SkillMutationPayload.self, from: data)
            if let index = skills.firstIndex(where: { $0.id == skillID }) {
                skills[index] = payload.skill
            } else {
                skills.append(payload.skill)
            }
            skillsStatusText = enabled ? "Skill activé" : "Skill désactivé"
        } catch {
            skillsStatusText = "Impossible de modifier le skill: \(describeError(error))"
            appendLog(skillsStatusText)
        }
    }

    func testSkill(_ skillID: String) async {
        testingSkillIDs.insert(skillID)
        defer { testingSkillIDs.remove(skillID) }
        do {
            var request = URLRequest(url: serverBaseURL.appending(path: "/api/skills/\(skillID)/test"))
            request.httpMethod = "POST"
            let data = try await dataForAuthorizedRequest(request)
            let payload = try JSONDecoder().decode(SkillTestPayload.self, from: data)
            skillsStatusText = payload.message
            appendLog("Test skill \(payload.id): \(payload.status)")
            await refreshSkills()
        } catch {
            skillsStatusText = "Test du skill impossible: \(describeError(error))"
            appendLog(skillsStatusText)
        }
    }

    func refreshSelfUpdateStatus() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/self-update/status"))
            let payload = try JSONDecoder().decode(SelfUpdateResult.self, from: data)
            selfUpdateStatus = payload
            if let workingPath = payload.working_path, !workingPath.isEmpty {
                selfUpdateWorkingPath = workingPath
            }
            selfUpdateStatusText = payload.message ?? "Self Update prêt"
        } catch {
            selfUpdateStatusText = "Self Update indisponible: \(describeError(error))"
            appendLog(selfUpdateStatusText)
        }
    }

    func runSelfUpdateAction(_ action: SelfUpdateAction) async {
        guard !isSelfUpdateRunning else { return }
        isSelfUpdateRunning = true
        selfUpdateStatusText = "\(action.label) en cours..."
        defer { isSelfUpdateRunning = false }

        do {
            var request = URLRequest(url: serverBaseURL.appending(path: action.path))
            request.httpMethod = action.method
            if action.method == "POST" {
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: selfUpdatePayload(for: action))
            }
            let data = try await dataForAuthorizedRequest(request)
            let payload = try JSONDecoder().decode(SelfUpdateResult.self, from: data)
            selfUpdateLastResult = payload
            selfUpdateStatusText = payload.message ?? "\(action.label) terminé"
            if let candidate = payload.candidate_app ?? payload.build?.candidate_app, !candidate.isEmpty {
                selfUpdateCandidateApp = candidate
            }
            if let backup = payload.backup_app, !backup.isEmpty {
                selfUpdateRollbackBackupApp = backup
            }
            appendLog("Self Update: \(action.label) terminé")
            await refreshSelfUpdateStatus()
        } catch {
            selfUpdateStatusText = "\(action.label) impossible: \(describeError(error))"
            appendLog(selfUpdateStatusText)
        }
    }

    enum SelfUpdateAction {
        case status
        case validate
        case diagnose
        case buildCandidate
        case autoUpdate
        case requestLLMUpdate
        case promote
        case rollback

        var label: String {
            switch self {
            case .status: return "Statut"
            case .validate: return "Validation"
            case .diagnose: return "Diagnostic"
            case .buildCandidate: return "Build candidate"
            case .autoUpdate: return "Auto-update"
            case .requestLLMUpdate: return "Update IA"
            case .promote: return "Promotion"
            case .rollback: return "Rollback"
            }
        }

        var method: String {
            self == .status ? "GET" : "POST"
        }

        var path: String {
            switch self {
            case .status: return "/api/self-update/status"
            case .validate: return "/api/self-update/validate"
            case .diagnose: return "/api/self-update/diagnose"
            case .buildCandidate: return "/api/self-update/build-candidate"
            case .autoUpdate: return "/api/self-update/run-cycle"
            case .requestLLMUpdate: return "/api/self-update/request-llm-update"
            case .promote: return "/api/self-update/promote"
            case .rollback: return "/api/self-update/rollback"
            }
        }
    }

    private func selfUpdatePayload(for action: SelfUpdateAction) -> [String: Any] {
        switch action {
        case .promote:
            return [
                "candidate_app": selfUpdateCandidateApp,
                "target_app": selfUpdateTargetApp,
                "backup_root": selfUpdateBackupRoot,
                "confirmation": selfUpdateConfirmation
            ]
        case .rollback:
            return [
                "candidate_app": selfUpdateRollbackBackupApp,
                "target_app": selfUpdateTargetApp,
                "confirmation": selfUpdateConfirmation
            ]
        case .autoUpdate:
            return [
                "working_path": selfUpdateWorkingPath,
                "output_root": selfUpdateOutputRoot,
                "objective": selfUpdateObjective
            ]
        case .buildCandidate:
            return [
                "working_path": selfUpdateWorkingPath,
                "output_root": selfUpdateOutputRoot
            ]
        case .requestLLMUpdate:
            return [
                "working_path": selfUpdateWorkingPath,
                "objective": selfUpdateObjective
            ]
        case .validate, .diagnose:
            return ["working_path": selfUpdateWorkingPath]
        case .status:
            return [:]
        }
    }

    func refreshHealth() async {
        for attempt in 0...max(0, runtimeConfig.healthRetries) {
            do {
                let (data, response) = try await URLSession.shared.data(from: serverBaseURL.appending(path: "/health"))
                if let httpResponse = response as? HTTPURLResponse,
                   !(200..<300).contains(httpResponse.statusCode) {
                    isBackendReady = false
                    statusText = "Port 8000 occupé par un autre service"
                    appendLog(statusText)
                    return
                }
                let payload: HealthPayload
                do {
                    payload = try JSONDecoder().decode(HealthPayload.self, from: data)
                } catch {
                    isBackendReady = false
                    statusText = "Port 8000 répond, mais ce n’est pas Mac Agent OS"
                    appendLog(statusText)
                    return
                }
                isBackendReady = payload.ready
                statusText = payload.ready ? "Backend prêt" : "Backend non prêt"
                appendLog(statusText)
                return
            } catch {
                if attempt < runtimeConfig.healthRetries {
                    let delay = UInt64(runtimeConfig.healthRetryDelayMs) * 1_000_000
                    try? await Task.sleep(nanoseconds: delay)
                    continue
                }
                isBackendReady = false
                statusText = "Backend introuvable"
                appendLog("Backend introuvable")
            }
        }
    }

    func refreshSettings() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/settings"))
            let payload = try JSONDecoder().decode(SettingsPayload.self, from: data)
            provider = payload.provider
            currentModel = payload.model
            syncProviderSetupModeFromActiveProvider()
            settingsStatusText = "Configuration chargée"
            syncActiveConnectionFromCurrentSettings(baseURL: payload.base_url)
            appendLog("Configuration chargée: \(provider) • \(currentModel)")
        } catch {
            let message = describeError(error)
            settingsStatusText = message
            appendLog("Configuration indisponible: \(message)")
        }
    }

    func refreshChatGPTStatus() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/chatgpt/status"))
            let payload = try JSONDecoder().decode(ChatGPTStatusPayload.self, from: data)
            let bridgeConnected = payload.bridge?.connected ?? false
            chatGPTConnected = bridgeConnected || payload.connected || (payload.oauth?.connected ?? false)

            if let accountID = payload.oauth?.account_id, !accountID.isEmpty {
                chatGPTAccountLabel = "ChatGPT Bridge • compte \(String(accountID.suffix(8)))"
            } else if payload.bridge?.installed == true {
                chatGPTAccountLabel = bridgeConnected ? "Bridge disponible • session active" : "Bridge disponible • session absente"
            } else {
                chatGPTAccountLabel = chatGPTConnected ? "Session ChatGPT active" : "Session ChatGPT absente"
            }

            if chatGPTConnected {
                chatGPTStatusText = "Session ChatGPT Bridge active"
            } else if payload.running {
                chatGPTStatusText = "Connexion ChatGPT en cours..."
            } else if let bridge = payload.bridge, !bridge.error.isEmpty {
                chatGPTStatusText = userFacingProviderError(bridge.error, provider: "local_chatgpt_codex")
            } else if !payload.error.isEmpty {
                chatGPTStatusText = userFacingProviderError(payload.error, provider: "local_chatgpt_codex")
            } else {
                chatGPTStatusText = "Aucune session ChatGPT détectée"
            }
            if let index = providerConnections.firstIndex(where: { $0.id == "local_chatgpt_codex" }), let bridge = payload.bridge {
                if bridge.connected {
                    providerConnections[index].statusText = localBridgeStatusLine(
                        modelsCount: providerConnections[index].availableModels.isEmpty ? nil : providerConnections[index].availableModels.count,
                        connected: true,
                        installed: bridge.installed,
                        expired: bridge.expired ?? false
                    )
                    providerConnections[index].errorText = ""
                } else if bridge.installed {
                    providerConnections[index].statusText = localBridgeStatusLine(
                        connected: false,
                        installed: bridge.installed,
                        expired: bridge.expired ?? false
                    )
                    providerConnections[index].errorText = userFacingProviderError(bridge.error.isEmpty ? (bridge.login_hint ?? "") : bridge.error, provider: "local_chatgpt_codex")
                } else {
                    providerConnections[index].statusText = localBridgeStatusLine(connected: false, installed: false)
                    providerConnections[index].errorText = userFacingProviderError(bridge.error, provider: "local_chatgpt_codex")
                }
            }
            appendLog(chatGPTStatusText)
        } catch {
            chatGPTConnected = false
            chatGPTAccountLabel = "Compte non connecté"
            chatGPTStatusText = "État ChatGPT indisponible"
            appendLog("État ChatGPT indisponible")
        }
    }

    func refreshModelsForCurrentProvider() async {
        do {
            let connection = providerConnection(for: provider)
            var components = URLComponents(url: serverBaseURL.appending(path: "/api/llm/models/\(provider)"), resolvingAgainstBaseURL: false)!
            var queryItems: [URLQueryItem] = []
            if let connection, !connection.apiKey.isEmpty {
                queryItems.append(URLQueryItem(name: "api_key", value: connection.apiKey))
            }
            if let connection, !connection.baseURL.isEmpty {
                queryItems.append(URLQueryItem(name: "base_url", value: connection.baseURL))
            }
            components.queryItems = queryItems.isEmpty ? nil : queryItems
            let url = components.url ?? serverBaseURL.appending(path: "/api/llm/models/\(provider)")
            let data = try await dataForAuthorizedURL(url)
            let payload = try JSONDecoder().decode(ProviderModelsPayload.self, from: data)
            availableModels = payload.models
            if provider == "openai" {
                openAIModels = payload.models
            }
            if let index = providerConnections.firstIndex(where: { $0.id == provider }) {
                providerConnections[index].availableModels = payload.models
                if providerConnections[index].model.isEmpty, let first = payload.models.first {
                    providerConnections[index].model = first
                }
                providerConnections[index].statusText = payload.models.isEmpty ? "Aucun modèle disponible" : "\(payload.models.count) modèle(s) détecté(s)"
                if provider != "local_chatgpt_codex" || chatGPTConnected {
                    providerConnections[index].errorText = payload.models.isEmpty ? "Aucun modèle disponible pour ce provider." : ""
                }
                persistProviderConnections()
            }
            if !availableModels.contains(currentModel), let first = availableModels.first {
                currentModel = first
            }
            appendLog("Modèles chargés pour \(provider): \(availableModels.count)")
        } catch {
            availableModels = []
            if provider == "openai" {
                openAIModels = []
            }
            if let index = providerConnections.firstIndex(where: { $0.id == provider }) {
                providerConnections[index].statusText = "Connexion indisponible"
                providerConnections[index].errorText = userFacingProviderError(userFacingLLMError(describeError(error), provider: provider), provider: provider)
            }
            appendLog("Impossible de charger les modèles pour \(provider)")
        }
    }

    func saveSettings() async {
        do {
            let connection = providerConnection(for: provider)
            let body = try JSONSerialization.data(withJSONObject: [
                "provider": provider,
                "model": currentModel,
                "api_key": connection?.apiKey ?? "",
                "base_url": connection?.resolvedBaseURL ?? ""
            ])

            var request = URLRequest(url: serverBaseURL.appending(path: "/api/settings"))
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let _ = try await dataForAuthorizedRequest(request)
            settingsStatusText = "Configuration enregistrée"
            await refreshSettings()
            await refreshModelsForCurrentProvider()
            appendLog("Configuration enregistrée")
        } catch {
            settingsStatusText = describeError(error)
            appendLog("Impossible d’enregistrer la configuration: \(describeError(error))")
        }
    }

    func selectProvider(_ newProvider: String) async {
        provider = newProvider
        await refreshModelsForCurrentProvider()
        if let connection = providerConnection(for: newProvider) {
            if !connection.model.isEmpty {
                currentModel = connection.model
            } else if let first = availableModels.first {
                currentModel = first
            }
        } else if let first = availableModels.first {
            currentModel = first
        }
        await saveSettings()
        appendLog("Provider sélectionné: \(newProvider)")
    }

    func selectModel(_ model: String) async {
        currentModel = model
        if let index = providerConnections.firstIndex(where: { $0.id == provider }) {
            providerConnections[index].model = model
            persistProviderConnections()
        }
        await saveSettings()
        appendLog("Modèle sélectionné: \(model)")
    }

    func analyzeLogs() async {
        guard !logs.isEmpty, !isAnalyzingLogs else { return }
        isAnalyzingLogs = true
        appendLog("Analyse des logs demandée")
        defer { isAnalyzingLogs = false }

        let excerpt = logs.prefix(80).joined(separator: "\n")

        do {
            let body = try JSONSerialization.data(withJSONObject: [
                "provider": provider,
                "model": currentModel,
                "message": "Analyse ces logs applicatifs et résume en français: l’état général, les erreurs éventuelles, et les actions recommandées.\n\nLogs:\n\(excerpt)",
                "system_prompt": "Tu es un assistant d’analyse de logs macOS. Réponds de façon concise, structurée et utile.",
                "attachments": []
            ])

            var request = URLRequest(url: serverBaseURL.appending(path: "/api/llm/chat"))
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")

            let data = try await dataForAuthorizedRequest(request)
            let decoded = try JSONDecoder().decode(ChatReply.self, from: data)
            logAnalysis = decoded.type == "error" ? userFacingLLMError(decoded.content, provider: decoded.actual?.provider ?? decoded.provider) : decoded.content
            appendLog("Analyse des logs terminée")
        } catch {
            logAnalysis = "Impossible d’analyser les logs: \(describeError(error))"
            appendLog("Échec de l’analyse des logs")
        }
    }

    func analyzeBackendLogs() async {
        guard !backendLogEntries.isEmpty, !isAnalyzingLogs else { return }
        isAnalyzingLogs = true
        appendLog("Analyse des logs backend demandée")
        defer { isAnalyzingLogs = false }

        let excerpt = backendLogEntries.suffix(80).joined(separator: "\n")

        do {
            let body = try JSONSerialization.data(withJSONObject: [
                "provider": provider,
                "model": currentModel,
                "message": "Analyse ces logs backend et résume en français: état du serveur, erreurs éventuelles, et actions recommandées.\n\nLogs backend:\n\(excerpt)",
                "system_prompt": "Tu es un assistant d’analyse de logs backend. Réponds de façon concise, structurée et utile.",
                "attachments": []
            ])

            var request = URLRequest(url: serverBaseURL.appending(path: "/api/llm/chat"))
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")

            let data = try await dataForAuthorizedRequest(request)
            let decoded = try JSONDecoder().decode(ChatReply.self, from: data)
            logAnalysis = decoded.type == "error" ? userFacingLLMError(decoded.content, provider: decoded.actual?.provider ?? decoded.provider) : decoded.content
            appendLog("Analyse des logs backend terminée")
        } catch {
            logAnalysis = "Impossible d’analyser les logs backend: \(describeError(error))"
            appendLog("Échec de l’analyse des logs backend")
        }
    }

    func refreshDiagnostics() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/diagnostics"))
            let payload = try JSONDecoder().decode(DiagnosticsPayload.self, from: data)
            diagnosticsSummary = payload
            backendLogEntries = payload.log_entries
            appendLog("Diagnostics backend chargés")
        } catch {
            appendLog("Impossible de charger les diagnostics backend: \(describeError(error))")
        }
    }

    func refreshLocalModels() async {
        do {
            let data = try await dataForAuthorizedURL(serverBaseURL.appending(path: "/api/models"))
            let payload = try JSONDecoder().decode(LocalModelsPayload.self, from: data)
            localExperimentalModels = payload.models.filter {
                $0.localizedCaseInsensitiveContains("heretic") || $0.localizedCaseInsensitiveContains("uncensored")
            }
        } catch {
            localExperimentalModels = []
        }
    }

    private func ensureActiveConversation() {
        guard !conversations.isEmpty else {
            let first = Conversation(
                title: "Bienvenue",
                messages: [
                    .init(
                        role: .assistant,
                        text: "Salut, que puis-je faire pour toi ?",
                        meta: "ChatGPT • \(currentModel)"
                    )
                ]
            )
            conversations = [first]
            currentConversationID = first.id
            sidebarSelection = .conversation(first.id)
            persistConversations()
            return
        }

        if currentConversationID == nil || conversations.contains(where: { $0.id == currentConversationID }) == false {
            currentConversationID = conversations[0].id
        }

        if let currentConversationID {
            sidebarSelection = .conversation(currentConversationID)
        }
    }

    private func appendMessage(_ message: ChatMessage) {
        ensureWritableConversation()
        guard let index = currentConversationIndex else { return }
        conversations[index].messages.append(message)
        conversations[index].updatedAt = .now
        if message.role == .assistant {
            if !userIsReviewingHistory || shouldForceScrollOnNextAssistantMessage {
                pendingScrollMessageID = message.id
            }
            shouldForceScrollOnNextAssistantMessage = false
        } else if message.role == .user || message.role == .system {
            pendingScrollMessageID = message.id
        }
        retitleConversationIfNeeded(at: index)
        sortConversations()
        persistConversations()
    }

    private func ensureWritableConversation() {
        if conversations.isEmpty {
            createConversation()
            return
        }

        if let currentConversationID,
           conversations.contains(where: { $0.id == currentConversationID }) {
            return
        }

        currentConversationID = conversations[0].id
        sidebarSelection = .conversation(conversations[0].id)
    }

    private var currentConversationIndex: Int? {
        guard let id = currentConversationID else { return nil }
        return conversations.firstIndex(where: { $0.id == id })
    }

    private func sortConversations() {
        conversations.sort { $0.updatedAt > $1.updatedAt }
    }

    private func retitleConversationIfNeeded(at index: Int) {
        guard conversations.indices.contains(index) else { return }
        let firstUserMessage = conversations[index].messages.first(where: { $0.role == .user })
        guard let firstUserMessage else { return }

        let newTitle = makeTitle(from: firstUserMessage.text)
        if conversations[index].title == "Nouvelle conversation" || conversations[index].title == "Bienvenue" {
            conversations[index].title = newTitle
        }
    }

    private func makeTitle(from text: String) -> String {
        let cleaned = text
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !cleaned.isEmpty else { return "Nouvelle conversation" }
        return String(cleaned.prefix(42))
    }

    private func buildSystemPrompt() -> String {
        let noJSON = "Ne génère jamais de JSON, de champs type/objective/plan/steps ni aucune structure de données. Réponds uniquement en langage naturel."
        if turboEnabled {
            return "Tu es un assistant concis pour macOS. Mode Turbo actif: conserve la logique de contexte compact mais ne tronque pas les messages utilisateur ou l’historique utile. Réponds de façon nette et courte. \(noJSON)"
        }
        if reasoningEnabled {
            return "Tu es un assistant concis pour macOS. Réfléchis soigneusement avant de répondre et donne une réponse nette. \(noJSON)"
        }
        return "Tu es un assistant concis pour macOS. \(noJSON)"
    }

    func providerLabel(for id: String) -> String {
        switch id {
        case "openai":
            return "OpenAI Platform"
        case "local_chatgpt_codex":
            return "ChatGPT / Codex Bridge"
        case "ollama":
            return "Ollama"
        case "anthropic":
            return "Anthropic"
        case "gemini":
            return "Gemini"
        case "huggingface":
            return "Hugging Face"
        case "openai_compatible":
            return "Custom OpenAI-compatible"
        default:
            return providerConnections.first(where: { $0.id == id })?.label ?? id.capitalized
        }
    }

    func providerShortDescription(for id: String) -> String {
        switch id {
        case "ollama":
            return "Modèles locaux exécutés sur cette machine."
        case "openai":
            return "Connexion via clé API OpenAI."
        case "local_chatgpt_codex":
            return "Connexion via le bridge ChatGPT embarqué."
        case "anthropic":
            return "Connexion via clé API Anthropic."
        case "gemini":
            return "Connexion via clé API Gemini."
        case "huggingface":
            return "Utilise les Inference Providers Hugging Face via token HF."
        case "openai_compatible":
            return "Serveur compatible OpenAI personnalisé."
        default:
            return "Provider IA configurable."
        }
    }

    func providerStatusSummary(_ connection: ProviderConnection) -> String {
        if !connection.enabled {
            return "Provider indisponible"
        }
        if !connection.errorText.isEmpty {
            return connection.errorText
        }
        if connection.availableModels.isEmpty {
            return connection.statusText.isEmpty ? "Aucun modèle disponible pour ce provider." : connection.statusText
        }
        return "\(connection.availableModels.count) modèle(s) détecté(s)"
    }

    private func assistantMeta(for reply: ChatReply) -> String {
        let actualProvider = reply.actual?.provider ?? reply.provider ?? provider
        let actualModel = reply.actual?.model ?? reply.model ?? currentModel
        let providerName = providerLabel(for: actualProvider)

        if turboEnabled {
            return "\(providerName) • \(actualModel) • Turbo"
        }
        return reasoningEnabled ? "\(providerName) • \(actualModel) • Réflexion" : "\(providerName) • \(actualModel)"
    }

    private func executionInfo(for reply: ChatReply) -> String? {
        let requestedProvider = reply.requested?.provider
        let requestedModel = reply.requested?.model
        let actualProvider = reply.actual?.provider ?? reply.provider
        let actualModel = reply.actual?.model ?? reply.model
        let fallback = reply.route?.fallback_reason?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        let requested = [requestedProvider, requestedModel].compactMap { $0 }.joined(separator: " • ")
        let actual = [actualProvider, actualModel].compactMap { $0 }.joined(separator: " • ")

        if !requested.isEmpty, !actual.isEmpty, requested != actual {
            var value = "Demandé: \(requested) → Utilisé: \(actual)"
            if !fallback.isEmpty {
                value += " • Fallback: \(fallback)"
            }
            return value
        }

        if !actual.isEmpty {
            var value = "Utilisé: \(actual)"
            if !fallback.isEmpty {
                value += " • Fallback: \(fallback)"
            }
            return value
        }

        return nil
    }

    private func planLocalAction(for text: String) async throws -> ChatMessage.LocalActionApproval? {
        var request = URLRequest(url: serverBaseURL.appending(path: "/api/local-actions/plan"))
        request.timeoutInterval = 8
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["message": text])

        let data = try await dataForAuthorizedRequest(request)
        let reply = try JSONDecoder().decode(LocalActionPlanReply.self, from: data)

        guard reply.type == "approval_required", let action = reply.action else {
            return nil
        }

        let actionTitle: String
        let payload: [String: String]
        let steps: [ChatMessage.LocalActionRequest]?

        switch action.type {
        case "analyze_mac":
            actionTitle = "Analyser ce Mac"
            payload = [:]
            steps = nil
        case "analyze_storage":
            actionTitle = "Analyser le stockage"
            payload = [:]
            steps = nil
        case "open_app":
            let appName = action.payload.app_name ?? "Application"
            actionTitle = "Ouvrir \(appName)"
            payload = ["app_name": appName]
            steps = nil
        case "open_url":
            let url = action.payload.url ?? ""
            actionTitle = "Ouvrir \(url)"
            payload = ["url": url]
            steps = nil
        case "create_file":
            let targetPath = action.payload.target_path ?? "~/Desktop/fichier.txt"
            actionTitle = "Créer \(targetPath)"
            payload = [
                "target_path": targetPath,
                "content": action.payload.content ?? ""
            ]
            steps = nil
        case "append_file":
            let targetPath = action.payload.target_path ?? "~/Desktop/fichier.txt"
            actionTitle = "Modifier \(targetPath)"
            payload = [
                "target_path": targetPath,
                "content": action.payload.content ?? ""
            ]
            steps = nil
        case "read_file":
            let targetPath = action.payload.target_path ?? "~/Desktop/fichier.txt"
            actionTitle = "Lire \(targetPath)"
            payload = ["target_path": targetPath]
            steps = nil
        case "code_task":
            let targetPath = action.payload.target_path ?? "~/Desktop/code.html"
            actionTitle = "Coder \(targetPath)"
            payload = [
                "target_path": targetPath,
                "instruction": action.payload.instruction ?? text
            ]
            steps = nil
        case "summarize_folder_to_file":
            let sourcePath = action.payload.source_path ?? ""
            let outputPath = action.payload.output_path ?? "~/Desktop/resume.txt"
            actionTitle = "Résumer \(sourcePath) vers \(outputPath)"
            payload = [
                "source_path": sourcePath,
                "output_path": outputPath
            ]
            steps = nil
        case "multi_step_plan":
            actionTitle = reply.objective ?? "Plan multi-étapes"
            payload = [:]
            steps = action.steps?.compactMap { step in
                switch step.type {
                case "analyze_mac", "analyze_storage":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: [:],
                        steps: nil
                    )
                case "open_app":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: ["app_name": step.payload.app_name ?? "Safari"],
                        steps: nil
                    )
                case "open_url":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: ["url": step.payload.url ?? ""],
                        steps: nil
                    )
                case "create_file":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: [
                            "target_path": step.payload.target_path ?? "~/Desktop/notes.txt",
                            "content": step.payload.content ?? ""
                        ],
                        steps: nil
                    )
                case "append_file":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: [
                            "target_path": step.payload.target_path ?? "~/Desktop/notes.txt",
                            "content": step.payload.content ?? ""
                        ],
                        steps: nil
                    )
                case "read_file":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: ["target_path": step.payload.target_path ?? "~/Desktop/notes.txt"],
                        steps: nil
                    )
                case "code_task":
                    return ChatMessage.LocalActionRequest(
                        type: step.type,
                        payload: [
                            "target_path": step.payload.target_path ?? "~/Desktop/code.html",
                            "instruction": step.payload.instruction ?? text
                        ],
                        steps: nil
                    )
                default:
                    return nil
                }
            }
        default:
            return nil
        }

        return ChatMessage.LocalActionApproval(
            objective: reply.objective ?? "Action locale détectée",
            plan: reply.plan ?? ["Valider la demande", "Exécuter l’action locale", "Afficher le résultat"],
            actionTitle: actionTitle,
            request: .init(type: action.type, payload: payload, steps: steps),
            status: .pending,
            resultText: ""
        )
    }

    private func localActionApproval(for messageID: UUID) -> ChatMessage.LocalActionApproval? {
        guard let conversationIndex = currentConversationIndex,
              let messageIndex = conversations[conversationIndex].messages.firstIndex(where: { $0.id == messageID }) else {
            return nil
        }
        return conversations[conversationIndex].messages[messageIndex].localActionApproval
    }

    private func updateLocalAction(messageID: UUID, mutate: (inout ChatMessage.LocalActionApproval) -> Void) {
        guard let conversationIndex = currentConversationIndex,
              let messageIndex = conversations[conversationIndex].messages.firstIndex(where: { $0.id == messageID }),
              var approval = conversations[conversationIndex].messages[messageIndex].localActionApproval else {
            return
        }
        mutate(&approval)
        conversations[conversationIndex].messages[messageIndex].localActionApproval = approval
        conversations[conversationIndex].updatedAt = .now
        pendingScrollMessageID = messageID
        persistConversations()
    }

    private func buildVisibleUserMessage(from text: String) -> String {
        let base = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !composerAttachments.isEmpty else { return base }

        let attachmentSummary = composerAttachments
            .map { "\($0.kind.rawValue): \($0.name)" }
            .joined(separator: "\n")

        if base.isEmpty {
            return "Pièces jointes:\n\(attachmentSummary)"
        }

        return "\(base)\n\nPièces jointes:\n\(attachmentSummary)"
    }

    private func buildOutgoingMessage(from text: String) -> String {
        let base = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !composerAttachments.isEmpty else { return base }

        let attachmentPayload = composerAttachments.map { attachment in
            """
            [Fichier]
            Nom: \(attachment.name)
            Type: \(attachment.kind.rawValue)
            Contenu:
            \(attachment.payload)
            """
        }.joined(separator: "\n\n")

        if base.isEmpty {
            return attachmentPayload
        }

        return "\(base)\n\n\(attachmentPayload)"
    }

    private func makeAttachment(from url: URL) -> ComposerAttachment? {
        let ext = url.pathExtension.lowercased()
        let textExtensions = Set(["txt", "md", "json", "csv", "swift", "py", "js", "ts", "html", "css", "xml", "yaml", "yml"])
        let imageExtensions = Set(["png", "jpg", "jpeg", "webp", "gif", "heic"])

        if textExtensions.contains(ext), let data = try? Data(contentsOf: url), let text = String(data: data, encoding: .utf8) {
            let payload = String(text.prefix(12000))
            return ComposerAttachment(url: url, name: url.lastPathComponent, kind: .text, payload: payload)
        }

        if imageExtensions.contains(ext) {
            let payload = buildImagePayload(from: url)
            return ComposerAttachment(url: url, name: url.lastPathComponent, kind: .image, payload: payload)
        }

        let payload = "Document local disponible ici: \(url.path)"
        return ComposerAttachment(url: url, name: url.lastPathComponent, kind: .document, payload: payload)
    }

    private func buildImagePayload(from url: URL) -> String {
        var parts: [String] = ["Image locale: \(url.path)"]

        if let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil),
           let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [CFString: Any],
           let width = properties[kCGImagePropertyPixelWidth] as? Int,
           let height = properties[kCGImagePropertyPixelHeight] as? Int {
            parts.append("Dimensions: \(width)x\(height)")
        }

        if let recognizedText = recognizeText(in: url), !recognizedText.isEmpty {
            parts.append("Texte détecté:\n\(recognizedText)")
        } else {
            parts.append("Texte détecté: aucun texte exploitable")
        }

        return parts.joined(separator: "\n")
    }

    private func recognizeText(in url: URL) -> String? {
        guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
            return nil
        }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true

        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
            let strings = request.results?
                .compactMap { $0.topCandidates(1).first?.string }
                .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } ?? []
            return strings.prefix(30).joined(separator: "\n")
        } catch {
            return nil
        }
    }

    private func persistConversations() {
        do {
            try FileManager.default.createDirectory(
                at: conversationsDirectory,
                withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(conversations)
            try data.write(to: conversationsURL, options: .atomic)
        } catch {
            NSSound.beep()
        }
    }

    private func mergeProviderConnections(_ descriptors: [ProviderConnectionDescriptor]) {
        let saved = Dictionary(uniqueKeysWithValues: providerConnections.map { ($0.id, $0) })
        providerConnections = descriptors.map { descriptor in
            var connection = saved[descriptor.id] ?? ProviderConnection(
                id: descriptor.id,
                label: descriptor.label,
                authMode: descriptor.auth_mode,
                enabled: descriptor.enabled,
                supportsAPIKey: descriptor.supports_api_key,
                supportsBaseURL: descriptor.supports_base_url,
                supportsModelListing: descriptor.supports_model_listing,
                supportsConnectionTest: descriptor.supports_connection_test,
                message: descriptor.message,
                apiKey: "",
                baseURL: descriptor.id == "ollama" ? "http://localhost:11434" : "",
                model: "",
                availableModels: [],
                statusText: descriptor.enabled ? "Non testé" : "Bientôt disponible",
                errorText: ""
            )
            connection.label = descriptor.label
            connection.authMode = descriptor.auth_mode
            connection.enabled = descriptor.enabled
            connection.supportsAPIKey = descriptor.supports_api_key
            connection.supportsBaseURL = descriptor.supports_base_url
            connection.supportsModelListing = descriptor.supports_model_listing
            connection.supportsConnectionTest = descriptor.supports_connection_test
            connection.label = providerLabel(for: descriptor.id)
            connection.message = providerShortDescription(for: descriptor.id)
            if !descriptor.enabled {
                connection.statusText = "Provider indisponible"
            } else if descriptor.id == "local_chatgpt_codex", let runtime = descriptor.runtime {
                if runtime.connected == true {
                    connection.statusText = localBridgeStatusLine(connected: true, installed: runtime.installed, expired: runtime.expired ?? false)
                    connection.errorText = ""
                } else if runtime.installed == true {
                    connection.statusText = localBridgeStatusLine(connected: false, installed: true, expired: runtime.expired ?? false)
                    connection.errorText = userFacingProviderError(runtime.error ?? runtime.login_hint ?? "", provider: descriptor.id)
                } else {
                    connection.statusText = localBridgeStatusLine(connected: false, installed: false)
                    connection.errorText = userFacingProviderError(runtime.error ?? "", provider: descriptor.id)
                }
            }
            return connection
        }
        persistProviderConnections()
    }

    private func localBridgeStatusLine(modelsCount: Int? = nil, connected: Bool? = nil, installed: Bool? = nil, expired: Bool? = nil) -> String {
        let isInstalled = installed ?? true
        let isConnected = connected ?? chatGPTConnected
        let isExpired = expired ?? false

        if !isInstalled {
            return "Bridge absent"
        }
        if isExpired {
            return "Bridge disponible • session expirée"
        }
        if !isConnected {
            return "Bridge disponible • aucune session détectée"
        }
        if let modelsCount {
            return "Bridge disponible • session active • \(modelsCount) modèle(s)"
        }
        return "Bridge disponible • session active"
    }

    private func providerConnection(for id: String) -> ProviderConnection? {
        providerConnections.first(where: { $0.id == id })
    }

    private func syncActiveConnectionFromCurrentSettings(baseURL: String? = nil) {
        guard let index = providerConnections.firstIndex(where: { $0.id == provider }) else { return }
        providerConnections[index].model = currentModel
        if let baseURL, !baseURL.isEmpty {
            providerConnections[index].baseURL = baseURL
        }
        if providerConnections[index].id == "local_chatgpt_codex" {
            if chatGPTConnected {
                providerConnections[index].statusText = localBridgeStatusLine(
                    modelsCount: providerConnections[index].availableModels.isEmpty ? nil : providerConnections[index].availableModels.count,
                    connected: true
                )
                providerConnections[index].errorText = ""
            }
        } else {
            providerConnections[index].statusText = "Configuration active"
            providerConnections[index].errorText = ""
        }
        persistProviderConnections()
    }

    private func persistProviderConnections() {
        do {
            try FileManager.default.createDirectory(at: conversationsDirectory, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(providerConnections)
            try data.write(to: providerConnectionsURL, options: .atomic)
        } catch {
            appendLog("Impossible de sauvegarder les connexions providers")
        }
    }

    private func loadProviderConnections() {
        do {
            let data = try Data(contentsOf: providerConnectionsURL)
            providerConnections = try JSONDecoder().decode([ProviderConnection].self, from: data)
        } catch {
            providerConnections = []
        }
    }

    func updateProviderConnection(_ id: String, apiKey: String? = nil, baseURL: String? = nil, model: String? = nil) {
        guard let index = providerConnections.firstIndex(where: { $0.id == id }) else { return }
        if let apiKey {
            providerConnections[index].apiKey = apiKey
        }
        if let baseURL {
            providerConnections[index].baseURL = baseURL
        }
        if let model {
            providerConnections[index].model = model
        }
        persistProviderConnections()
    }

    func listModels(for connectionID: String) async {
        guard let connection = providerConnection(for: connectionID), connection.supportsModelListing else { return }
        do {
            var components = URLComponents(url: serverBaseURL.appending(path: "/api/llm/models/\(connectionID)"), resolvingAgainstBaseURL: false)!
            var queryItems: [URLQueryItem] = []
            if !connection.apiKey.isEmpty {
                queryItems.append(URLQueryItem(name: "api_key", value: connection.apiKey))
            }
            if !connection.baseURL.isEmpty {
                queryItems.append(URLQueryItem(name: "base_url", value: connection.baseURL))
            }
            components.queryItems = queryItems.isEmpty ? nil : queryItems
            let url = components.url ?? serverBaseURL.appending(path: "/api/llm/models/\(connectionID)")
            let data = try await dataForAuthorizedURL(url)
            let payload = try JSONDecoder().decode(ProviderModelsPayload.self, from: data)
            if let index = providerConnections.firstIndex(where: { $0.id == connectionID }) {
                providerConnections[index].availableModels = payload.models
                if providerConnections[index].model.isEmpty, let first = payload.models.first {
                    providerConnections[index].model = first
                }
                providerConnections[index].statusText = payload.models.isEmpty ? "Aucun modèle disponible" : "\(payload.models.count) modèle(s) détecté(s)"
                if connectionID != "local_chatgpt_codex" || chatGPTConnected {
                    providerConnections[index].errorText = payload.models.isEmpty ? "Aucun modèle disponible pour ce provider." : ""
                }
                persistProviderConnections()
            }
            if connectionID == provider {
                availableModels = payload.models
                if !payload.models.contains(currentModel), let first = payload.models.first {
                    currentModel = first
                }
            }
        } catch {
            if let index = providerConnections.firstIndex(where: { $0.id == connectionID }) {
                providerConnections[index].statusText = "Connexion indisponible"
                providerConnections[index].errorText = userFacingProviderError(userFacingLLMError(describeError(error), provider: connectionID), provider: connectionID)
            }
        }
    }

    func testProviderConnection(_ connectionID: String) async {
        guard let connection = providerConnection(for: connectionID), connection.supportsConnectionTest else { return }
        do {
            let body = try JSONSerialization.data(withJSONObject: [
                "provider": connectionID,
                "api_key": connection.apiKey,
                "model": connection.model.isEmpty ? currentModel : connection.model,
                "base_url": connection.baseURL
            ])
            var request = URLRequest(url: serverBaseURL.appending(path: "/api/llm/test"))
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let data = try await dataForAuthorizedRequest(request)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            let status = payload["status"] as? String ?? "error"
            let response = payload["response"] as? [String: Any]
            let responseContent = response?["content"] as? String ?? payload["error"] as? String ?? ""
            if let index = providerConnections.firstIndex(where: { $0.id == connectionID }) {
                if status == "success" {
                    if connectionID == "local_chatgpt_codex" {
                        let count = providerConnections[index].availableModels.isEmpty ? nil : providerConnections[index].availableModels.count
                        providerConnections[index].statusText = localBridgeStatusLine(modelsCount: count, connected: true)
                    } else {
                        providerConnections[index].statusText = "Connecté"
                    }
                    providerConnections[index].errorText = ""
                } else {
                    providerConnections[index].statusText = "Connexion échouée"
                    providerConnections[index].errorText = responseContent.isEmpty ? "Vérification impossible" : userFacingProviderError(userFacingLLMError(responseContent, provider: connectionID), provider: connectionID)
                }
                persistProviderConnections()
            }
        } catch {
            if let index = providerConnections.firstIndex(where: { $0.id == connectionID }) {
                providerConnections[index].statusText = "Connexion échouée"
                providerConnections[index].errorText = userFacingProviderError(userFacingLLMError(describeError(error), provider: connectionID), provider: connectionID)
            }
        }
    }

    func saveProviderConnection(_ connectionID: String, activate: Bool) async {
        guard let connection = providerConnection(for: connectionID), connection.enabled else { return }
        if activate {
            let selectedModel = connection.model.trimmingCharacters(in: .whitespacesAndNewlines)
            if connectionID == "local_chatgpt_codex", !chatGPTConnected {
                settingsStatusText = "Connecte le bridge ChatGPT / Codex avant de l’utiliser."
                appendLog(settingsStatusText)
                return
            }
            if selectedModel.isEmpty {
                settingsStatusText = "Choisis un modèle avant d’activer ce provider."
                appendLog(settingsStatusText)
                return
            }
            if connectionID == "openai_compatible",
               connection.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                settingsStatusText = "Ajoute l’URL du provider compatible OpenAI."
                appendLog(settingsStatusText)
                return
            }
            if connection.supportsAPIKey,
               connectionID != "openai_compatible",
               connection.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                settingsStatusText = connectionID == "huggingface" ? "Ajoute un token Hugging Face." : "Ajoute une clé API pour utiliser ce provider."
                appendLog(settingsStatusText)
                return
            }
            provider = connectionID
            syncProviderSetupModeFromActiveProvider()
            currentModel = selectedModel
            await saveSettings()
        } else {
            persistProviderConnections()
            settingsStatusText = "Configuration locale enregistrée"
        }
    }

    private func syncProviderSetupModeFromActiveProvider() {
        switch provider {
        case "ollama":
            selectedProviderMode = .ollama
        case "local_chatgpt_codex":
            selectedProviderMode = .bridge
        case "openai", "anthropic", "gemini", "huggingface", "openai_compatible":
            selectedProviderMode = .apiKey
            selectedAPIProviderID = provider
        default:
            selectedProviderMode = .apiKey
        }
    }

    private func appendLog(_ message: String) {
        let timestamp = Date.now.formatted(date: .omitted, time: .standard)
        logs.insert("[\(timestamp)] \(message)", at: 0)
        if logs.count > 300 {
            logs = Array(logs.prefix(300))
        }
    }

    private func startLiveActivity(_ title: String) {
        liveActivityTitle = title
        liveActivityDetails = []
    }

    private func pushLiveActivity(_ detail: String) {
        liveActivityDetails.append(detail)
        if liveActivityDetails.count > 6 {
            liveActivityDetails = Array(liveActivityDetails.suffix(6))
        }
    }

    private func finishLiveActivity(_ finalDetail: String) {
        pushLiveActivity(finalDetail)
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            liveActivityTitle = ""
            liveActivityDetails = []
        }
    }

    private func failLiveActivity(_ finalDetail: String) {
        pushLiveActivity(finalDetail)
    }

    private func loadConversations() {
        do {
            let data = try Data(contentsOf: conversationsURL)
            conversations = try JSONDecoder().decode([Conversation].self, from: data)
            migrateLegacyWelcomeMessages()
            sortConversations()
        } catch {
            conversations = []
        }
    }

    private func migrateLegacyWelcomeMessages() {
        let legacyMessages = [
            "Bienvenue. Cette base native SwiftUI parle au backend de MacAgent-OS et prépare la migration vers une vraie app macOS Apple.",
            "Bonjour. Je suis prêt quand tu veux."
        ]
        for conversationIndex in conversations.indices {
            for messageIndex in conversations[conversationIndex].messages.indices {
                if legacyMessages.contains(conversations[conversationIndex].messages[messageIndex].text) {
                    let existing = conversations[conversationIndex].messages[messageIndex]
                    conversations[conversationIndex].messages[messageIndex] = ChatMessage(
                        id: existing.id,
                        role: existing.role,
                        text: "Salut, que puis-je faire pour toi ?",
                        meta: existing.meta,
                        createdAt: existing.createdAt
                    )
                }
            }
        }
    }

    private var conversationsDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return base.appendingPathComponent("MacAgentOS", isDirectory: true)
    }

    private var conversationsURL: URL {
        conversationsDirectory.appendingPathComponent("conversations.json")
    }

    private var providerConnectionsURL: URL {
        conversationsDirectory.appendingPathComponent("provider-connections.json")
    }
}

struct RootView: View {
    @Bindable var appState: AppState

    var body: some View {
        NavigationSplitView {
            SidebarView(appState: appState)
        } detail: {
            Group {
                switch appState.sidebarSelection {
                case .localModels:
                    LocalModelsView(appState: appState)
                case .skills:
                    SkillsView(appState: appState)
                case .selfUpdate:
                    SelfUpdateView(appState: appState)
                case .diagnostics:
                    DiagnosticsView(appState: appState)
                case .logs:
                    LogsView(appState: appState)
                case .settings:
                    SettingsView(appState: appState)
                case .conversation, nil:
                    ChatScreen(appState: appState)
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .onChange(of: appState.sidebarSelection) { _, newValue in
            appState.handleSelectionChange(newValue)
        }
    }
}

struct SidebarView: View {
    @Bindable var appState: AppState

    var body: some View {
        List(selection: $appState.sidebarSelection) {
            Section {
                Button {
                    appState.createConversation()
                } label: {
                    Label(appState.l("Nouvelle conversation", "New conversation"), systemImage: "square.and.pencil")
                }
                .buttonStyle(PremiumPressButtonStyle())
            }

            Section(appState.l("Historique", "History")) {
                ForEach(appState.conversations) { conversation in
                    ConversationRow(conversation: conversation)
                        .tag(AppState.SidebarSelection.conversation(conversation.id))
                        .contentShape(Rectangle())
                        .onTapGesture {
                            appState.selectConversation(conversation.id)
                        }
                }
            }

            Section(appState.l("Modèles", "Models")) {
                Label(appState.l("Modèles locaux", "Local models"), systemImage: "sparkles.rectangle.stack")
                    .tag(AppState.SidebarSelection.localModels)
                Label("Skills", systemImage: "square.grid.2x2")
                    .tag(AppState.SidebarSelection.skills)
            }

            Section(appState.l("Système", "System")) {
                Label("Self Update", systemImage: "arrow.triangle.2.circlepath")
                    .tag(AppState.SidebarSelection.selfUpdate)
                Label("Diagnostics", systemImage: "stethoscope")
                    .tag(AppState.SidebarSelection.diagnostics)
                Label("Logs", systemImage: "list.bullet.rectangle")
                    .tag(AppState.SidebarSelection.logs)
            }

            Section("IA") {
                Button {
                    appState.sidebarSelection = .settings
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("Providers IA", systemImage: "cpu")
                            .font(.headline)
                        Text("\(appState.providerDisplayName) • \(appState.currentModel)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(PremiumPressButtonStyle())
                .tag(AppState.SidebarSelection.settings)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Mac Agent OS")
        .scrollContentBackground(.hidden)
        .background(
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90),
                    UIPalette.hover.opacity(0.82)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    appState.createConversation()
                } label: {
                    Label(appState.l("Nouvelle conversation", "New conversation"), systemImage: "square.and.pencil")
                }
            }
        }
        .animation(.easeInOut(duration: 0.16), value: appState.sidebarSelection)
    }
}

struct ConversationRow: View {
    let conversation: AppState.Conversation
    @State private var isHovered = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(conversation.title)
                .font(.body.weight(.medium))
                .lineLimit(1)
            Text(conversation.updatedAt.formatted(date: .abbreviated, time: .shortened))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.white.opacity(isHovered ? 0.07 : 0.0))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color.white.opacity(isHovered ? 0.08 : 0.0), lineWidth: 0.8)
        )
        .onHover { hovering in
            isHovered = hovering
        }
        .animation(.easeInOut(duration: 0.14), value: isHovered)
    }
}

struct ChatScreen: View {
    @Bindable var appState: AppState

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90),
                    UIPalette.hover.opacity(0.82)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                HStack(spacing: 14) {
                    VStack(alignment: .leading, spacing: 4) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Mac Agent OS")
                                .font(.title.weight(.semibold))
                            Text(appState.currentConversationTitle)
                                .font(.headline.weight(.medium))
                        }
                        Text("By Zk")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(UIPalette.textSecondary)
                        Text(appState.chatHeaderSubtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 8) {
                        HStack(spacing: 8) {
                            Menu {
                                ForEach(appState.availableModels, id: \.self) { model in
                                    Button(model) {
                                        Task { await appState.selectModel(model) }
                                    }
                                }
                            } label: {
                                ChipLabel(title: appState.currentModel, systemImage: "cpu")
                            }
                            if appState.turboEnabled {
                                ChipLabel(title: "Turbo", systemImage: "flame.fill")
                            }
                            ChipLabel(title: appState.reasoningEnabled ? appState.reasoningLabel : appState.fastLabel, systemImage: appState.reasoningEnabled ? "brain" : "bolt.fill")
                        }

                        VStack(alignment: .trailing, spacing: 4) {
                            Label(appState.localizedStatusText, systemImage: appState.isBackendReady ? "checkmark.circle.fill" : "xmark.circle")
                                .font(.caption)
                                .foregroundStyle(appState.isBackendReady ? UIPalette.success : UIPalette.textSecondary)

                            if appState.provider.lowercased() == "openai" {
                                Label(appState.chatGPTAccountLabel, systemImage: appState.chatGPTConnected ? "person.crop.circle.badge.checkmark" : "person.crop.circle.badge.exclam")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 18)
                .premiumSurface(cornerRadius: 0)

                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 14) {
                            if appState.isSending || !appState.liveActivityTitle.isEmpty {
                                LiveActivityCard(
                                    title: appState.liveActivityTitle.isEmpty ? "Activité du modèle" : appState.liveActivityTitle,
                                    details: appState.liveActivityDetails,
                                    isRunning: appState.isSending
                                )
                            }
                            ForEach(appState.currentMessages) { message in
                                MessageBubble(message: message, appState: appState)
                                    .id(message.id)
                            }
                        }
                        .padding(24)
                        .frame(maxWidth: .infinity)
                    }
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 8)
                            .onChanged { _ in
                                appState.userIsReviewingHistory = true
                            }
                    )
                    .onChange(of: appState.pendingScrollMessageID) { _, messageID in
                        guard let messageID else { return }
                        withAnimation(.easeOut(duration: 0.22)) {
                            proxy.scrollTo(messageID, anchor: .bottom)
                        }
                        appState.pendingScrollMessageID = nil
                    }
                }

                Divider()

                VStack(spacing: 12) {
                    if !appState.composerAttachments.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 10) {
                                ForEach(appState.composerAttachments) { attachment in
                                    AttachmentChip(attachment: attachment) {
                                        appState.removeAttachment(attachment)
                                    }
                                }
                            }
                            .padding(.horizontal, 18)
                        }
                    }

                    HStack(alignment: .bottom, spacing: 12) {
                        Button {
                            appState.importAttachments()
                        } label: {
                            Image(systemName: "plus")
                                .font(.system(size: 15, weight: .semibold))
                                .frame(width: 34, height: 34)
                        }
                        .buttonStyle(.plain)
                        .background(
                            Circle()
                                .fill(Color.white.opacity(0.10))
                        )
                        .help("Ajouter des fichiers")

                        Button {
                            appState.chooseProjectFolder()
                        } label: {
                            Image(systemName: appState.activeProjectPath.isEmpty ? "folder.badge.plus" : "folder.fill")
                                .font(.system(size: 15, weight: .semibold))
                                .frame(width: 34, height: 34)
                        }
                        .buttonStyle(.plain)
                        .background(
                            Circle()
                                .fill(appState.activeProjectPath.isEmpty ? Color.white.opacity(0.10) : UIPalette.accent.opacity(0.30))
                        )
                        .help(appState.activeProjectPath.isEmpty ? appState.l("Choisir un projet", "Choose a project") : appState.l("Projet actif: \(appState.activeProjectPath)", "Active project: \(appState.activeProjectPath)"))

                        VStack(spacing: 10) {
                            TextField(appState.l("Message à Mac Agent OS...", "Message Mac Agent OS..."), text: $appState.inputText, axis: .vertical)
                                .lineLimit(1...8)
                                .textFieldStyle(.plain)
                                .foregroundStyle(.white)
                                .onSubmit {
                                    Task { await appState.send() }
                                }

                            HStack {
                                Toggle(isOn: $appState.reasoningEnabled) {
                                    Label(appState.reasoningLabel, systemImage: appState.reasoningEnabled ? "brain" : "bolt.fill")
                                }
                                .toggleStyle(.switch)
                                .controlSize(.small)

                                Toggle(isOn: $appState.turboEnabled) {
                                    Label("Turbo", systemImage: "flame.fill")
                                }
                                .toggleStyle(.switch)
                                .controlSize(.small)

                                Spacer()

                                Text(composerHint)
                                    .font(.caption)
                                    .foregroundStyle(.white.opacity(0.7))
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                        .premiumSurface(cornerRadius: 20)

                        Button {
                            Task { await appState.send() }
                        } label: {
                            if appState.isSending {
                                ProgressView()
                                    .controlSize(.small)
                                    .frame(width: 38, height: 38)
                            } else {
                                Image(systemName: "arrow.up")
                                    .font(.system(size: 14, weight: .bold))
                                    .frame(width: 38, height: 38)
                            }
                        }
                        .buttonStyle(.plain)
                        .background(
                            Circle()
                                .fill(sendButtonFill)
                        )
                        .foregroundStyle(.white)
                        .keyboardShortcut(.return, modifiers: [])
                        .disabled((appState.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && appState.composerAttachments.isEmpty) || appState.isSending)
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 18)
                }
                .padding(.top, 12)
                .premiumSurface(cornerRadius: 0)
            }
        }
    }

    private var sendButtonFill: some ShapeStyle {
        LinearGradient(colors: [UIPalette.accent, UIPalette.accent.opacity(0.72)], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    private var composerHint: String {
        if !appState.activeProjectPath.isEmpty {
            return appState.l("Projet: \(URL(fileURLWithPath: appState.activeProjectPath).lastPathComponent)", "Project: \(URL(fileURLWithPath: appState.activeProjectPath).lastPathComponent)")
        }
        return appState.composerAttachments.isEmpty ? appState.l("Texte, image, document", "Text, image, document") : appState.l("\(appState.composerAttachments.count) pièce(s) jointe(s)", "\(appState.composerAttachments.count) attachment(s)")
    }
}

struct MessageBubble: View {
    let message: AppState.ChatMessage
    @Bindable var appState: AppState

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 96) }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                Text(message.meta)
                    .font(.caption2)
                    .foregroundStyle(.secondary)

                if let executionInfo = message.executionInfo, !executionInfo.isEmpty {
                    Text(executionInfo)
                        .font(.caption2)
                        .foregroundStyle(.secondary.opacity(0.9))
                }

                if let approval = message.localActionApproval {
                    LocalActionApprovalCard(
                        approval: approval,
                        onApprove: { Task { await appState.approveLocalAction(messageID: message.id) } },
                        onRefuse: { appState.refuseLocalAction(messageID: message.id) }
                    )
                } else {
                    Text(message.text)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .background(bubbleBackground)
                        .foregroundStyle(foregroundStyle)
                        .textSelection(.enabled)
                        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .strokeBorder(borderColor, lineWidth: 0.8)
                        )
                        .shadow(color: .black.opacity(0.06), radius: 10, y: 4)
                        .contextMenu {
                            Button("Copier") {
                                NSPasteboard.general.clearContents()
                                NSPasteboard.general.setString(message.text, forType: .string)
                            }
                        }
                }
            }
            .frame(maxWidth: 640, alignment: message.role == .user ? .trailing : .leading)

            if message.role != .user { Spacer(minLength: 96) }
        }
    }

    @ViewBuilder
    private var bubbleBackground: some View {
        switch message.role {
        case .user:
            LinearGradient(colors: [UIPalette.accent.opacity(0.90), UIPalette.accent.opacity(0.70)], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .assistant:
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Color.white.opacity(0.10))
        case .system:
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Color.white.opacity(0.12))
        }
    }

    private var foregroundStyle: Color {
        switch message.role {
        case .user:
            return .white
        case .assistant:
            return .white
        case .system:
            return .white
        }
    }

    private var borderColor: Color {
        switch message.role {
        case .user:
            return .white.opacity(0.16)
        case .assistant:
            return .white.opacity(0.14)
        case .system:
            return .white.opacity(0.16)
        }
    }
}

struct LocalActionApprovalCard: View {
    let approval: AppState.ChatMessage.LocalActionApproval
    let onApprove: () -> Void
    let onRefuse: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            // ── Header ────────────────────────────────────────────────────
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [UIPalette.accent, UIPalette.accent.opacity(0.70)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 32, height: 32)
                    Text("💡")
                        .font(.system(size: 16))
                }

                VStack(alignment: .leading, spacing: 1) {
                    Text("Action détectée")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                    Text(approval.objective)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.65))
                        .lineLimit(2)
                }
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            Divider()
                .overlay(Color.white.opacity(0.10))

            // ── Plan ──────────────────────────────────────────────────────
            VStack(alignment: .leading, spacing: 6) {
                Text("Étapes prévues")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.45))
                    .textCase(.uppercase)
                    .tracking(0.5)

                ForEach(Array(approval.plan.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .top, spacing: 8) {
                        Text("\(index + 1)")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.35))
                            .frame(width: 14, alignment: .trailing)
                        Text(step)
                            .font(.callout)
                            .foregroundStyle(.white.opacity(0.88))
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            // ── Action label ──────────────────────────────────────────────
            HStack(spacing: 8) {
                Image(systemName: "arrow.right.circle.fill")
                    .font(.caption)
                    .foregroundStyle(UIPalette.accent.opacity(0.78))
                Text(approval.actionTitle)
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.white.opacity(0.90))
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 14)

            // ── Result / status ───────────────────────────────────────────
            if approval.status == .running {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                        .tint(UIPalette.textSecondary)
                    Text("Exécution en cours…")
                        .font(.caption)
                        .foregroundStyle(UIPalette.textSecondary)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 14)
            } else if !approval.resultText.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Divider().overlay(Color.white.opacity(0.08))
                    Text(approval.resultText)
                        .font(.caption)
                        .foregroundStyle(statusColor)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                }
            }

            // ── Buttons ───────────────────────────────────────────────────
            if approval.status == .pending {
                Divider().overlay(Color.white.opacity(0.10))

                HStack(spacing: 10) {
                    Button(action: onApprove) {
                        Label("Confirmer", systemImage: "checkmark")
                            .font(.callout.weight(.semibold))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(UIPalette.accent)
                    .controlSize(.regular)

                    Button(action: {}) {
                        Label("Modifier", systemImage: "pencil")
                            .font(.callout)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    .disabled(true)
                    .opacity(0.45)

                    Spacer()

                    Button(action: onRefuse) {
                        Text("Annuler")
                            .font(.callout)
                            .foregroundStyle(.white.opacity(0.55))
                    }
                    .buttonStyle(.plain)
                    .controlSize(.regular)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            } else if approval.status != .running {
                HStack(spacing: 6) {
                    Image(systemName: statusIcon)
                        .font(.caption.weight(.semibold))
                    Text(statusLabel)
                        .font(.caption.weight(.semibold))
                }
                .foregroundStyle(statusColor)
                .padding(.horizontal, 16)
                .padding(.bottom, 14)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(Color.white.opacity(0.07))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(
                    LinearGradient(
                        colors: [UIPalette.accent.opacity(0.30), UIPalette.border.opacity(1.2)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ),
                    lineWidth: 1
                )
        )
        .shadow(color: .black.opacity(0.16), radius: 16, y: 6)
        .frame(maxWidth: 520)
    }

    private var statusLabel: String {
        switch approval.status {
        case .pending:   return "En attente"
        case .running:   return "Exécution en cours"
        case .completed: return "Action exécutée"
        case .cancelled: return "Action annulée"
        case .failed:    return "Échec"
        }
    }

    private var statusIcon: String {
        switch approval.status {
        case .pending:   return "clock"
        case .running:   return "arrow.trianglehead.clockwise"
        case .completed: return "checkmark.circle.fill"
        case .cancelled: return "xmark.circle"
        case .failed:    return "exclamationmark.triangle.fill"
        }
    }

    private var statusColor: Color {
        switch approval.status {
        case .pending:   return .secondary
        case .running:   return UIPalette.warning
        case .completed: return UIPalette.success
        case .cancelled: return .secondary
        case .failed:    return UIPalette.error
        }
    }
}

struct LiveActivityCard: View {
    let title: String
    let details: [String]
    let isRunning: Bool

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    if isRunning {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(UIPalette.success)
                    }

                    Text(title)
                        .font(.headline)
                        .foregroundStyle(.white)
                }

                ForEach(details, id: \.self) { detail in
                    Text("• \(detail)")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.82))
                }
            }
            .padding(16)
            .premiumSurface(cornerRadius: 18)

            Spacer(minLength: 96)
        }
    }
}

struct AttachmentChip: View {
    let attachment: AppState.ComposerAttachment
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: iconName)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(attachment.name)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                Text(attachment.kind.rawValue)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Button(action: remove) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            Capsule(style: .continuous)
                .fill(Color.white.opacity(0.10))
        )
    }

    private var iconName: String {
        switch attachment.kind {
        case .text:
            return "doc.text"
        case .image:
            return "photo"
        case .document:
            return "paperclip"
        }
    }
}

struct ChipLabel: View {
    let title: String
    let systemImage: String

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(.white)
            .background(
                Capsule(style: .continuous)
                    .fill(Color.white.opacity(0.10))
            )
    }
}

private struct PremiumSurfaceModifier: ViewModifier {
    let cornerRadius: CGFloat
    let highlighted: Bool
    let hovered: Bool

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(
                        (hovered ? UIPalette.hover : UIPalette.surface)
                            .opacity(highlighted ? 0.985 : 0.92)
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(
                        highlighted
                            ? UIPalette.accent.opacity(hovered ? 0.50 : 0.42)
                            : UIPalette.border.opacity(hovered ? 1.5 : 1.0),
                        lineWidth: highlighted ? 1.15 : 1
                    )
            )
            .shadow(color: .black.opacity(hovered ? 0.24 : 0.18), radius: hovered ? 11 : 8, x: 0, y: hovered ? 4 : 3)
            .offset(y: hovered ? -1 : 0)
            .animation(.easeInOut(duration: 0.15), value: hovered)
            .animation(.easeInOut(duration: 0.15), value: highlighted)
    }
}

private struct ProviderInputStyleModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .fill(UIPalette.hover.opacity(0.55))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .strokeBorder(UIPalette.border.opacity(1.2), lineWidth: 0.8)
            )
    }
}

private struct ProviderInfoLabelModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .font(.caption)
            .foregroundStyle(UIPalette.textSecondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(UIPalette.surface.opacity(0.68))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(UIPalette.border, lineWidth: 0.8)
            )
    }
}

private struct ProviderErrorLabelModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .font(.caption)
            .foregroundStyle(UIPalette.error.opacity(0.95))
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(UIPalette.error.opacity(0.12))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(UIPalette.error.opacity(0.24), lineWidth: 0.9)
            )
    }
}

private struct PremiumPressButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.988 : 1.0)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private extension View {
    func premiumSurface(cornerRadius: CGFloat = 16, highlighted: Bool = false, hovered: Bool = false) -> some View {
        modifier(PremiumSurfaceModifier(cornerRadius: cornerRadius, highlighted: highlighted, hovered: hovered))
    }

    func providerInputStyle() -> some View {
        modifier(ProviderInputStyleModifier())
    }

    func providerInfoLabel() -> some View {
        modifier(ProviderInfoLabelModifier())
    }

    func providerErrorLabel() -> some View {
        modifier(ProviderErrorLabelModifier())
    }
}

struct SkillsView: View {
    @Bindable var appState: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Skills")
                            .font(.title2.weight(.semibold))
                        Text("Modules desktop activables pour guider le chat et les actions locales.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        Task { await appState.refreshSkills() }
                    } label: {
                        Label("Rafraîchir", systemImage: "arrow.clockwise")
                    }
                    .buttonStyle(.bordered)
                }

                if !appState.skillsStatusText.isEmpty {
                    Text(appState.skillsStatusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(appState.skills) { skill in
                        SkillCard(
                            skill: skill,
                            isTesting: appState.testingSkillIDs.contains(skill.id),
                            onToggle: {
                                Task { await appState.setSkillEnabled(skill.id, enabled: !skill.enabled) }
                            },
                            onTest: {
                                Task { await appState.testSkill(skill.id) }
                            }
                        )
                    }
                }
            }
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
        }
        .task {
            if appState.skills.isEmpty {
                await appState.refreshSkills()
            }
        }
    }
}

struct SkillCard: View {
    let skill: AppState.SkillDescriptor
    let isTesting: Bool
    let onToggle: () -> Void
    let onTest: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(skill.name)
                        .font(.headline.weight(.semibold))
                    Text(skill.description)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 8) {
                    StatusPill(
                        title: skill.enabled ? "Activé" : "Désactivé",
                        systemImage: skill.enabled ? "checkmark.circle.fill" : "pause.circle",
                        color: skill.enabled ? UIPalette.success : UIPalette.textSecondary
                    )
                    StatusPill(
                        title: skill.available ? "Disponible" : "Indisponible",
                        systemImage: skill.available ? "bolt.horizontal.circle.fill" : "exclamationmark.triangle.fill",
                        color: skill.available ? UIPalette.accent : UIPalette.warning
                    )
                }
            }

            HStack(spacing: 8) {
                StatusPill(title: skill.category, systemImage: "folder", color: UIPalette.accent)
                StatusPill(title: "Risque \(skill.risk)", systemImage: riskIcon, color: riskColor)
                if !skill.allowed_tools.isEmpty {
                    StatusPill(title: skill.allowed_tools.joined(separator: ", "), systemImage: "wrench.and.screwdriver", color: UIPalette.textSecondary)
                }
            }

            if !skill.available {
                Label(skill.availability_message, systemImage: "info.circle")
                    .providerInfoLabel()
            }

            if !skill.examples.isEmpty {
                Text(skill.examples.prefix(2).joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 10) {
                Button {
                    onToggle()
                } label: {
                    Label(skill.enabled ? "Désactiver" : "Activer", systemImage: skill.enabled ? "pause.fill" : "play.fill")
                }
                .buttonStyle(.bordered)

                Button {
                    onTest()
                } label: {
                    Label(isTesting ? "Test..." : "Test rapide", systemImage: "checklist")
                }
                .buttonStyle(.borderedProminent)
                .disabled(isTesting)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .premiumSurface(cornerRadius: 18, highlighted: skill.enabled)
    }

    private var riskIcon: String {
        switch skill.risk {
        case "high": return "exclamationmark.octagon.fill"
        case "medium": return "exclamationmark.triangle.fill"
        default: return "checkmark.shield.fill"
        }
    }

    private var riskColor: Color {
        switch skill.risk {
        case "high": return UIPalette.error
        case "medium": return UIPalette.warning
        default: return UIPalette.success
        }
    }
}

struct SettingsView: View {
    @Bindable var appState: AppState

    private func requiresAPIKey(_ providerID: String) -> Bool {
        providerID != "openai_compatible"
    }

    private func hasRequiredAPIInputs(_ connection: AppState.ProviderConnection) -> Bool {
        let hasSecret = !requiresAPIKey(connection.id)
            || !connection.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let hasBaseURL = connection.id != "openai_compatible"
            || !connection.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return hasSecret && hasBaseURL
    }

    private func canActivateAPIProvider(_ connection: AppState.ProviderConnection) -> Bool {
        hasRequiredAPIInputs(connection)
            && !connection.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 30) {
                languagePanel
                providerHeader

                Picker(appState.l("Mode IA", "AI mode"), selection: $appState.selectedProviderMode) {
                    Text(appState.l("Clé API", "API key")).tag(AppState.ProviderSetupMode.apiKey)
                    Text("ChatGPT / Codex Bridge").tag(AppState.ProviderSetupMode.bridge)
                    Text("Local / Ollama").tag(AppState.ProviderSetupMode.ollama)
                }
                .pickerStyle(.segmented)
                .padding(.trailing, 4)

                HStack(spacing: 14) {
                    ProviderModeCard(
                        title: appState.l("Clé API", "API key"),
                        subtitle: appState.l("Utilise OpenAI, Anthropic, Gemini, Hugging Face ou un provider compatible via clé API.", "Use OpenAI, Anthropic, Gemini, Hugging Face, or an OpenAI-compatible provider with an API key."),
                        systemImage: "key.fill",
                        selected: appState.selectedProviderMode == .apiKey
                    ) { appState.selectedProviderMode = .apiKey }
                    ProviderModeCard(
                        title: "ChatGPT / Codex Bridge",
                        subtitle: appState.l("Connecte ChatGPT depuis l’app, sans installation CLI par l’utilisateur.", "Connect ChatGPT from inside the app, with no CLI install required for normal use."),
                        systemImage: "person.crop.circle.badge.checkmark",
                        selected: appState.selectedProviderMode == .bridge
                    ) { appState.selectedProviderMode = .bridge }
                    ProviderModeCard(
                        title: "Local / Ollama",
                        subtitle: appState.l("Utilise des modèles locaux via Ollama.", "Use local models through Ollama."),
                        systemImage: "desktopcomputer",
                        selected: appState.selectedProviderMode == .ollama
                    ) { appState.selectedProviderMode = .ollama }
                }
                .frame(maxWidth: 980)

                switch appState.selectedProviderMode {
                case .apiKey:
                    apiKeyMode
                case .bridge:
                    bridgeMode
                case .ollama:
                    ollamaMode
                }

                responseOptions
            }
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .animation(.easeInOut(duration: 0.16), value: appState.selectedProviderMode)
            .animation(.easeInOut(duration: 0.16), value: appState.selectedAPIProviderID)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 24)
    }

    private var languagePanel: some View {
        ProviderSetupPanel(title: appState.l("Langue", "Language")) {
            Picker(appState.l("Langue de l’interface", "Interface language"), selection: $appState.appLanguage) {
                ForEach(AppState.AppLanguage.allCases) { language in
                    Text(language.label).tag(language)
                }
            }
            .pickerStyle(.segmented)
            Text(appState.l("La langue est enregistrée sur ce Mac et s’applique aux écrans principaux.", "The language is saved on this Mac and applies to the main screens."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var providerHeader: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(appState.l("Providers IA", "AI Providers"))
                    .font(.title2.weight(.semibold))
                Text(appState.l("Choisis comment Mac Agent OS se connecte à un modèle.", "Choose how Mac Agent OS connects to a model."))
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 10) {
                StatusPill(title: appState.l("Actif", "Active"), systemImage: "checkmark.circle.fill", color: .blue)
                Text("\(appState.providerDisplayName) · \(appState.currentModel)")
                    .font(.headline.weight(.medium))
                    .foregroundStyle(.primary.opacity(0.95))
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .premiumSurface(cornerRadius: 16)
        }
    }

    @ViewBuilder
    private var apiKeyMode: some View {
        if let index = appState.providerConnections.firstIndex(where: { $0.id == appState.selectedAPIProviderID }) {
            ProviderSetupPanel(title: "Connexion par clé API") {
                Picker("Fournisseur", selection: $appState.selectedAPIProviderID) {
                    Text("OpenAI").tag("openai")
                    Text("Anthropic").tag("anthropic")
                    Text("Gemini").tag("gemini")
                    Text("Hugging Face").tag("huggingface")
                    Text("Custom OpenAI-compatible").tag("openai_compatible")
                }
                .pickerStyle(.menu)

                SecureField(
                    appState.providerConnections[index].id == "huggingface" ? "hf_..." :
                        (appState.providerConnections[index].id == "openai_compatible" ? "Clé API optionnelle" : "Clé API"),
                    text: $appState.providerConnections[index].apiKey
                )
                .providerInputStyle()

                if requiresAPIKey(appState.providerConnections[index].id),
                   appState.providerConnections[index].apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Label(
                        appState.providerConnections[index].id == "huggingface"
                            ? "Ajoute un token Hugging Face."
                            : "Ajoute une clé API pour utiliser ce provider.",
                        systemImage: "key"
                    )
                    .providerInfoLabel()
                }
                if appState.providerConnections[index].id == "openai_compatible" {
                    TextField("Base URL compatible OpenAI", text: $appState.providerConnections[index].baseURL)
                        .providerInputStyle()
                    if appState.providerConnections[index].baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        Label("Ajoute une URL complète, par exemple http://localhost:1234/v1.", systemImage: "link")
                            .providerInfoLabel()
                    }
                }
                if appState.providerConnections[index].id == "huggingface" {
                    Label(
                        "Hugging Face peut proposer des crédits/free tier selon le compte et le modèle, mais ce n’est pas illimité ni garanti.",
                        systemImage: "info.circle"
                    )
                    .providerInfoLabel()
                }

                if !appState.providerConnections[index].availableModels.isEmpty {
                    Picker("Modèle", selection: $appState.providerConnections[index].model) {
                        ForEach(appState.providerConnections[index].availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                } else {
                    Text("Charge les modèles après avoir ajouté la clé API.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                if !appState.providerConnections[index].errorText.isEmpty {
                    Label(appState.providerConnections[index].errorText, systemImage: "exclamationmark.triangle.fill")
                        .providerErrorLabel()
                        .transition(.opacity.combined(with: .scale(scale: 0.99)))
                }

                DisclosureGroup("Options avancées") {
                    VStack(alignment: .leading, spacing: 10) {
                        if appState.providerConnections[index].supportsBaseURL && appState.providerConnections[index].id != "openai_compatible" {
                            TextField("Base URL", text: $appState.providerConnections[index].baseURL)
                                .providerInputStyle()
                        }
                        TextField("Modèle manuel", text: $appState.providerConnections[index].model)
                            .providerInputStyle()
                    }
                    .padding(.top, 8)
                }

                HStack(spacing: 10) {
                    Button("Charger les modèles") {
                        Task { await appState.listModels(for: appState.providerConnections[index].id) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    .disabled(!hasRequiredAPIInputs(appState.providerConnections[index]))
                    Button("Utiliser ce modèle") {
                        Task { await appState.saveProviderConnection(appState.providerConnections[index].id, activate: true) }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.regular)
                    .disabled(!canActivateAPIProvider(appState.providerConnections[index]))
                }
            }
        }
    }

    @ViewBuilder
    private var bridgeMode: some View {
        if let index = appState.providerConnections.firstIndex(where: { $0.id == "local_chatgpt_codex" }) {
            let connection = appState.providerConnections[index]
            ProviderSetupPanel(title: "ChatGPT / Codex Bridge") {
                Text("Utilise le bridge ChatGPT embarqué dans l’app. Aucun outil CLI externe n’est requis en usage normal.")
                    .foregroundStyle(.secondary)

                HStack(spacing: 10) {
                    StatusPill(title: connection.statusText.contains("absent") || connection.statusText.contains("non installé") ? "Bridge absent" : "Bridge disponible", systemImage: "point.3.connected.trianglepath.dotted", color: connection.statusText.contains("absent") || connection.statusText.contains("non installé") ? UIPalette.warning : UIPalette.success)
                    StatusPill(title: appState.chatGPTConnected ? "Session détectée" : "Session absente", systemImage: appState.chatGPTConnected ? "checkmark.circle.fill" : "person.crop.circle.badge.exclamationmark", color: appState.chatGPTConnected ? UIPalette.success : UIPalette.warning)
                    StatusPill(title: connection.availableModels.isEmpty ? "Aucun modèle" : "\(connection.availableModels.count) modèle(s)", systemImage: "cpu", color: connection.availableModels.isEmpty ? UIPalette.warning : UIPalette.accent)
                }

                if !connection.errorText.isEmpty {
                    Label(connection.errorText, systemImage: "exclamationmark.triangle.fill")
                        .providerErrorLabel()
                        .textSelection(.enabled)
                        .transition(.opacity.combined(with: .scale(scale: 0.99)))
                }

                if !connection.availableModels.isEmpty {
                    Picker("Modèle", selection: $appState.providerConnections[index].model) {
                        ForEach(connection.availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                } else if appState.chatGPTConnected {
                    Label("Charge les modèles du bridge avant de l’utiliser.", systemImage: "cpu")
                        .providerInfoLabel()
                }

                HStack(spacing: 10) {
                    Button("Tester la connexion") {
                        Task { await appState.testProviderConnection("local_chatgpt_codex") }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    Button("Charger les modèles") {
                        Task { await appState.listModels(for: "local_chatgpt_codex") }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    if !appState.chatGPTConnected {
                        Button("Se connecter avec ChatGPT") {
                            Task { await appState.connectChatGPT() }
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.regular)
                    }
                    Button("Utiliser ce provider") {
                        Task { await appState.saveProviderConnection("local_chatgpt_codex", activate: true) }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.regular)
                    .disabled(
                        appState.provider == "local_chatgpt_codex"
                        || !appState.chatGPTConnected
                        || appState.providerConnections[index].model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
            }
        }
    }

    @ViewBuilder
    private var ollamaMode: some View {
        if let index = appState.providerConnections.firstIndex(where: { $0.id == "ollama" }) {
            ProviderSetupPanel(title: "Local / Ollama") {
                Text("Utilise des modèles locaux via Ollama.")
                    .foregroundStyle(.secondary)

                TextField("URL Ollama", text: $appState.providerConnections[index].baseURL)
                    .providerInputStyle()

                if !appState.providerConnections[index].availableModels.isEmpty {
                    Picker("Modèle local", selection: $appState.providerConnections[index].model) {
                        ForEach(appState.providerConnections[index].availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                } else {
                    Text("Aucun modèle local détecté.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                if !appState.providerConnections[index].errorText.isEmpty {
                    Label(appState.providerConnections[index].errorText, systemImage: "exclamationmark.triangle.fill")
                        .providerErrorLabel()
                        .transition(.opacity.combined(with: .scale(scale: 0.99)))
                }

                HStack(spacing: 10) {
                    Button("Tester") {
                        Task { await appState.testProviderConnection("ollama") }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    Button("Charger les modèles") {
                        Task { await appState.listModels(for: "ollama") }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.regular)
                    Button("Utiliser ce modèle") {
                        Task { await appState.saveProviderConnection("ollama", activate: true) }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.regular)
                    .disabled(appState.providerConnections[index].model.isEmpty)
                }
            }
        }
    }

    private var responseOptions: some View {
        ProviderSetupPanel(title: "Réponse") {
            VStack(alignment: .leading, spacing: 8) {
                Toggle("Mode Turbo", isOn: $appState.turboEnabled)
                Text("Turbo enlève la limite de caractères sur le contexte envoyé, tout en gardant la même logique de sélection de contexte et de routage.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Text(appState.settingsStatusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct ProviderModeCard: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let selected: Bool
    let action: () -> Void
    @State private var isHovered = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 10) {
                Image(systemName: systemImage)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(selected ? UIPalette.accent.opacity(0.96) : .secondary)
                Text(title)
                    .font(.headline.weight(.medium))
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, minHeight: 116, alignment: .topLeading)
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .premiumSurface(cornerRadius: 16, highlighted: selected, hovered: isHovered)
        }
        .onHover { hovering in
            isHovered = hovering
        }
        .buttonStyle(PremiumPressButtonStyle())
    }
}

struct ProviderSetupPanel<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title)
                .font(.headline.weight(.semibold))
            content()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .premiumSurface(cornerRadius: 18)
    }
}

struct ProviderConnectionCard: View {
    @Binding var connection: AppState.ProviderConnection
    let isActive: Bool
    let onTest: () -> Void
    let onLoadModels: () -> Void
    let onSave: () -> Void
    let onUse: () -> Void
    let onConnectLocalAuth: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(connection.label)
                            .font(.headline)
                        Text(connection.message)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if isActive {
                        StatusPill(title: "Actif", systemImage: "checkmark.circle.fill", color: UIPalette.accent)
                    }
                }

                HStack(spacing: 10) {
                    StatusPill(title: connection.enabled ? connectionStatusTitle : "Indisponible", systemImage: connectionStatusIcon, color: connectionStatusColor)
                    StatusPill(title: modelStatusTitle, systemImage: "cpu", color: modelStatusColor)
                }

                if connection.id == "local_chatgpt_codex" {
                    VStack(alignment: .leading, spacing: 4) {
                        Label(connection.statusText, systemImage: "terminal")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if !connection.errorText.isEmpty {
                            Label(connection.errorText, systemImage: "exclamationmark.triangle.fill")
                                .providerErrorLabel()
                                .textSelection(.enabled)
                        }
                    }
                } else if !connection.errorText.isEmpty {
                    Label(connection.errorText, systemImage: "exclamationmark.triangle.fill")
                        .providerErrorLabel()
                        .textSelection(.enabled)
                }

                if connection.supportsAPIKey {
                    SecureField("Clé API", text: $connection.apiKey)
                        .providerInputStyle()
                }

                if connection.supportsBaseURL {
                    TextField("Base URL", text: $connection.baseURL)
                        .providerInputStyle()
                }

                if !connection.availableModels.isEmpty {
                    Picker("Modèle", selection: $connection.model) {
                        ForEach(connection.availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                } else {
                    TextField("Modèle", text: $connection.model)
                        .providerInputStyle()
                }

                HStack(spacing: 10) {
                    if connection.supportsConnectionTest {
                        Button("Tester", action: onTest)
                    }
                    if connection.supportsModelListing {
                        Button("Charger les modèles", action: onLoadModels)
                    }
                    if connection.id == "local_chatgpt_codex" {
                        Button("Connecter / reconnecter", action: onConnectLocalAuth)
                    }
                    Button("Enregistrer", action: onSave)
                    if connection.enabled {
                        Button(isActive ? "Provider utilisé" : "Utiliser ce provider", action: onUse)
                            .disabled(isActive)
                    }
                }
        }
        .padding(16)
        .disabled(!connection.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
        .premiumSurface(cornerRadius: 18)
    }

    private var connectionStatusTitle: String {
        if !connection.errorText.isEmpty {
            return "Non connecté"
        }
        if connection.statusText.localizedCaseInsensitiveContains("connect")
            || connection.statusText.localizedCaseInsensitiveContains("session active")
            || connection.statusText.localizedCaseInsensitiveContains("valid") {
            return "Connecté"
        }
        if connection.statusText.localizedCaseInsensitiveContains("non testé") {
            return "Non testé"
        }
        return "À vérifier"
    }

    private var connectionStatusIcon: String {
        connectionStatusTitle == "Connecté" ? "checkmark.circle.fill" : "exclamationmark.circle"
    }

    private var connectionStatusColor: Color {
        if !connection.enabled {
            return .secondary
        }
        if connectionStatusTitle == "Connecté" {
            return UIPalette.success
        }
        if connectionStatusTitle == "Non testé" {
            return .secondary
        }
        return UIPalette.warning
    }

    private var modelStatusTitle: String {
        connection.availableModels.isEmpty ? "Aucun modèle" : "\(connection.availableModels.count) modèle(s)"
    }

    private var modelStatusColor: Color {
        connection.availableModels.isEmpty ? UIPalette.warning : UIPalette.accent
    }
}

struct StatusPill: View {
    let title: String
    let systemImage: String
    let color: Color
    @State private var isHovered = false

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .foregroundStyle(color.opacity(0.92))
            .background(
                Capsule(style: .continuous)
                    .fill(color.opacity(isHovered ? 0.18 : 0.14))
            )
            .overlay(
                Capsule(style: .continuous)
                    .strokeBorder(UIPalette.border.opacity(1.2), lineWidth: 0.8)
            )
            .onHover { hovering in
                isHovered = hovering
            }
            .transition(.opacity.combined(with: .scale(scale: 0.98)))
            .animation(.easeInOut(duration: 0.16), value: isHovered)
    }
}

struct DiagnosticsView: View {
    @Bindable var appState: AppState

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Diagnostics")
                                .font(.title.weight(.semibold))
                            Text("État global de l’app et du backend")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Rafraîchir") {
                            Task { await appState.refreshDiagnostics() }
                        }
                        .buttonStyle(.bordered)
                    }

                    diagnosticCard(title: "Backend") {
                        LabeledContent("État", value: appState.diagnosticsSummary?.ready == true ? "Prêt" : "Indisponible")
                        LabeledContent("Provider", value: appState.diagnosticsSummary?.settings.provider ?? appState.provider)
                        LabeledContent("Modèle", value: appState.diagnosticsSummary?.settings.model ?? appState.currentModel)
                        LabeledContent("API UI", value: appState.isBackendReady ? "Connectée" : appState.statusText)
                    }

                    diagnosticCard(title: "Points d’attention") {
                        let warnings = diagnosticWarnings
                        if warnings.isEmpty {
                            Label("Aucun blocage détecté sur le backend et la configuration courante.", systemImage: "checkmark.circle.fill")
                                .font(.callout)
                                .foregroundStyle(.blue)
                        } else {
                            ForEach(warnings, id: \.self) { warning in
                                Label(warning, systemImage: "exclamationmark.triangle.fill")
                                    .font(.callout)
                                    .providerErrorLabel()
                            }
                        }
                    }

                    diagnosticCard(title: "ChatGPT") {
                        let bridge = appState.diagnosticsSummary?.bridge
                        LabeledContent("Bridge", value: bridge?.installed == true ? "Disponible" : "Absent")
                        LabeledContent("Session", value: bridge?.connected == true || appState.diagnosticsSummary?.chatgpt.connected == true ? "Connectée" : "Non connectée")
                        LabeledContent("Expiration", value: bridge?.expired == true || appState.diagnosticsSummary?.chatgpt.expired == true ? "Expirée" : "Valide")
                        if let error = bridge?.error, !error.isEmpty {
                            Text(appState.userFacingProviderError(error, provider: "local_chatgpt_codex"))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }

                    diagnosticCard(title: "Ollama") {
                        LabeledContent("État", value: appState.diagnosticsSummary?.ollama?.available == true ? "Disponible" : "Indisponible")
                        LabeledContent("URL", value: appState.diagnosticsSummary?.ollama?.base_url ?? "http://localhost:11434")
                        Text(appState.diagnosticsSummary?.ollama?.message ?? "État Ollama non chargé.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    diagnosticCard(title: "MCP") {
                        LabeledContent("Actifs", value: "\(appState.diagnosticsSummary?.mcp?.active_count ?? 0)")
                        LabeledContent("Ignorés", value: "\(appState.diagnosticsSummary?.mcp?.skipped_count ?? 0)")
                        let active = appState.diagnosticsSummary?.mcp?.active ?? []
                        if !active.isEmpty {
                            Text("Actifs: \(active.joined(separator: ", "))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        let skipped = appState.diagnosticsSummary?.mcp?.skipped ?? []
                        if !skipped.isEmpty {
                            Text("Ignorés optionnels: \(skipped.map(\.name).joined(separator: ", "))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    diagnosticCard(title: "Heretic") {
                        LabeledContent("État", value: appState.diagnosticsSummary?.heretic?.installed == true ? "Détecté" : "Indisponible")
                        LabeledContent("Version", value: appState.diagnosticsSummary?.heretic?.version ?? "Inconnue")
                    }

                    diagnosticCard(title: "Modèles locaux") {
                        LabeledContent("Nombre", value: "\(appState.diagnosticsSummary?.local_model_count ?? appState.localExperimentalModels.count)")
                        Text((appState.diagnosticsSummary?.local_models_preview ?? []).joined(separator: ", "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if !appState.backendLogEntries.isEmpty {
                        diagnosticCard(title: "Logs backend récents") {
                            ForEach(appState.backendLogEntries.suffix(8), id: \.self) { line in
                                Text(line)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(.white.opacity(0.92))
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
                .padding(24)
            }
        }
    }

    private var diagnosticWarnings: [String] {
        var warnings: [String] = []

        if appState.diagnosticsSummary?.ready != true {
            warnings.append("Backend indisponible: vérifie que `python server.py` tourne sur 127.0.0.1:8000.")
        }

        let provider = appState.diagnosticsSummary?.settings.provider ?? appState.provider
        if provider == "ollama", appState.diagnosticsSummary?.ollama?.available == false {
            warnings.append("Ollama ne répond pas sur localhost:11434. L’app reste utilisable avec un autre provider.")
        } else if provider == "ollama", (appState.diagnosticsSummary?.local_model_count ?? 0) == 0 {
            warnings.append("Ollama ne fournit aucun modèle local. Lance Ollama et installe au moins un modèle.")
        }

        if appState.chatGPTConnected == false, provider == "local_chatgpt_codex" {
            warnings.append("ChatGPT / Codex Bridge n’est pas connecté. Connecte ChatGPT ou choisis un autre provider.")
        }

        if let skipped = appState.diagnosticsSummary?.mcp?.skipped, !skipped.isEmpty {
            let names = skipped.prefix(3).map(\.name).joined(separator: ", ")
            warnings.append("MCP optionnels ignorés: \(names). Les MCP actifs restent utilisables.")
        }

        return Array(warnings.prefix(5))
    }

    @ViewBuilder
    private func diagnosticCard<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)
                .foregroundStyle(.white)
            content()
                .foregroundStyle(.white.opacity(0.9))
        }
        .padding(16)
        .premiumSurface(cornerRadius: 18)
    }
}

struct LogsView: View {
    @Bindable var appState: AppState

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Logs")
                            .font(.title.weight(.semibold))
                        Text("Activité récente de l’application")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        Task { await appState.analyzeLogs() }
                    } label: {
                        if appState.isAnalyzingLogs {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Label("Analyser", systemImage: "waveform.path.ecg.text")
                        }
                    }
                    .buttonStyle(.bordered)
                    Button {
                        Task { await appState.analyzeBackendLogs() }
                    } label: {
                        Label("Backend", systemImage: "server.rack")
                    }
                    .buttonStyle(.bordered)
                    Button("Rafraîchir") {
                        Task { await appState.refreshAll() }
                    }
                    .buttonStyle(.bordered)
                }

                if !appState.logAnalysis.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Interprétation")
                            .font(.headline)
                            .foregroundStyle(.white)
                        Text(appState.logAnalysis)
                            .foregroundStyle(.white.opacity(0.92))
                            .textSelection(.enabled)
                    }
                    .padding(16)
                    .background(
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .fill(Color.white.opacity(0.07))
                    )
                }

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(appState.logs, id: \.self) { entry in
                            Text(entry)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.white.opacity(0.92))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(12)
                                .premiumSurface(cornerRadius: 16)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
            .padding(24)
        }
    }
}

struct SelfUpdateView: View {
    @Bindable var appState: AppState

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    statusPanel
                    pathsPanel
                    actionPanel
                    resultPanel
                }
                .frame(maxWidth: 1040, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding(.horizontal, 28)
                .padding(.vertical, 24)
            }
        }
        .task {
            await appState.refreshSelfUpdateStatus()
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 5) {
                Text("Self Update Lab")
                    .font(.title.weight(.semibold))
                Text("Diagnostiquer, builder et promouvoir une candidate sans toucher à la copie SAFE.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await appState.refreshSelfUpdateStatus() }
            } label: {
                Label("Rafraîchir", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .disabled(appState.isSelfUpdateRunning)
        }
    }

    private var statusPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                StatusPill(
                    title: appState.selfUpdateStatus?.safe_exists == true ? "SAFE OK" : "SAFE absente",
                    systemImage: appState.selfUpdateStatus?.safe_exists == true ? "checkmark.shield.fill" : "exclamationmark.triangle.fill",
                    color: appState.selfUpdateStatus?.safe_exists == true ? UIPalette.success : UIPalette.warning
                )
                StatusPill(
                    title: appState.selfUpdateStatus?.working_exists == true ? "WORKING OK" : "WORKING absente",
                    systemImage: appState.selfUpdateStatus?.working_exists == true ? "hammer.circle.fill" : "exclamationmark.triangle.fill",
                    color: appState.selfUpdateStatus?.working_exists == true ? UIPalette.accent : UIPalette.warning
                )
                if appState.isSelfUpdateRunning {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            Text(appState.selfUpdateStatusText.isEmpty ? "Statut self-update non chargé." : appState.selfUpdateStatusText)
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)

            if let root = appState.selfUpdateStatus?.root {
                LabeledContent("Racine", value: root)
                    .textSelection(.enabled)
            }
        }
        .padding(16)
        .premiumSurface(cornerRadius: 18)
    }

    private var pathsPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Chemins")
                .font(.headline)

            SelfUpdatePathField(title: "Working copy", text: $appState.selfUpdateWorkingPath)
            SelfUpdatePathField(title: "Sortie candidate", text: $appState.selfUpdateOutputRoot)
            SelfUpdatePathField(title: "Candidate .app", text: $appState.selfUpdateCandidateApp)
            SelfUpdatePathField(title: "App cible", text: $appState.selfUpdateTargetApp)
            SelfUpdatePathField(title: "Dossier backup", text: $appState.selfUpdateBackupRoot)
            SelfUpdatePathField(title: "Backup à restaurer", text: $appState.selfUpdateRollbackBackupApp)

            SecureField("Confirmation promotion/rollback", text: $appState.selfUpdateConfirmation)
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))

            VStack(alignment: .leading, spacing: 5) {
                Text("Objectif IA")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                TextEditor(text: $appState.selfUpdateObjective)
                    .font(.system(.body, design: .monospaced))
                    .frame(minHeight: 82)
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.white.opacity(0.05))
                    )
            }
        }
        .padding(16)
        .premiumSurface(cornerRadius: 18)
    }

    private var actionPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Actions")
                    .font(.headline)
                Spacer()
                if appState.isSelfUpdateRunning {
                    Label("Action en cours...", systemImage: "hourglass")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(UIPalette.warning)
                }
            }

            HStack(spacing: 10) {
                Button {
                    Task { await appState.runSelfUpdateAction(.autoUpdate) }
                } label: {
                    Label("Lancer auto-update IA", systemImage: "wand.and.stars")
                }
                .buttonStyle(.borderedProminent)

                Button {
                    Task { await appState.runSelfUpdateAction(.requestLLMUpdate) }
                } label: {
                    Label("Proposition IA seule", systemImage: "brain.head.profile")
                }
                .buttonStyle(.bordered)

                Button {
                    Task { await appState.runSelfUpdateAction(.diagnose) }
                } label: {
                    Label("Diagnostiquer", systemImage: "stethoscope")
                }
                .buttonStyle(.bordered)
            }
            .disabled(appState.isSelfUpdateRunning)

            HStack(spacing: 10) {
                Button {
                    Task { await appState.runSelfUpdateAction(.validate) }
                } label: {
                    Label("Valider", systemImage: "checkmark.seal")
                }
                .buttonStyle(.bordered)

                Button {
                    Task { await appState.runSelfUpdateAction(.buildCandidate) }
                } label: {
                    Label("Builder candidate", systemImage: "shippingbox")
                }
                .buttonStyle(.bordered)
            }
            .disabled(appState.isSelfUpdateRunning)

            Text("Le bouton principal lance le cycle complet: diagnostic, demande IA, puis build candidate.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)

            HStack(spacing: 10) {
                Button(role: .destructive) {
                    Task { await appState.runSelfUpdateAction(.promote) }
                } label: {
                    Label("Promouvoir candidate", systemImage: "arrow.up.doc")
                }
                .buttonStyle(.bordered)
                .disabled(appState.isSelfUpdateRunning || appState.selfUpdateConfirmation != "PROMOTE_MAC_AGENT_OS_CANDIDATE")

                Button(role: .destructive) {
                    Task { await appState.runSelfUpdateAction(.rollback) }
                } label: {
                    Label("Rollback backup", systemImage: "arrow.uturn.backward.circle")
                }
                .buttonStyle(.bordered)
                .disabled(appState.isSelfUpdateRunning || appState.selfUpdateConfirmation != "ROLLBACK_MAC_AGENT_OS_BACKUP" || appState.selfUpdateRollbackBackupApp.isEmpty)
            }

            Text("Promotion: PROMOTE_MAC_AGENT_OS_CANDIDATE · Rollback: ROLLBACK_MAC_AGENT_OS_BACKUP")
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        }
        .padding(16)
        .premiumSurface(cornerRadius: 18)
    }

    @ViewBuilder
    private var resultPanel: some View {
        if let result = appState.selfUpdateLastResult {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Dernier résultat")
                        .font(.headline)
                    Spacer()
                    StatusPill(
                        title: result.status ?? "inconnu",
                        systemImage: result.status == "ok" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill",
                        color: result.status == "ok" ? UIPalette.success : UIPalette.warning
                    )
                }

                Text(result.message ?? "Action terminée.")
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)

                if let steps = result.steps, !steps.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Étapes")
                            .font(.subheadline.weight(.semibold))
                        ForEach(steps) { step in
                            VStack(alignment: .leading, spacing: 4) {
                                Label(
                                    step.name,
                                    systemImage: step.status == "ok" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                                )
                                .foregroundStyle(step.status == "ok" ? UIPalette.success : UIPalette.warning)
                                if let message = step.message, !message.isEmpty {
                                    Text(message)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .textSelection(.enabled)
                                }
                                if let path = step.path, !path.isEmpty {
                                    Text(path)
                                        .font(.caption.monospaced())
                                        .foregroundStyle(.secondary)
                                        .textSelection(.enabled)
                                }
                            }
                        }
                    }
                }

                if let suggestions = result.suggestions, !suggestions.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Suggestions")
                            .font(.subheadline.weight(.semibold))
                        ForEach(suggestions, id: \.self) { suggestion in
                            Label(suggestion, systemImage: "lightbulb")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if let candidate = result.candidate_app ?? result.build?.candidate_app {
                    LabeledContent("Candidate", value: candidate)
                        .textSelection(.enabled)
                }
                if let backup = result.backup_app {
                    LabeledContent("Backup", value: backup)
                        .textSelection(.enabled)
                }
                if let proposal = result.proposal_path {
                    LabeledContent("Proposition", value: proposal)
                        .textSelection(.enabled)
                }
                if let provider = result.provider {
                    LabeledContent("Provider IA", value: [provider, result.model].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " • "))
                        .textSelection(.enabled)
                }
                if let contextFiles = result.context_files, !contextFiles.isEmpty {
                    Text("Contexte envoyé: \(contextFiles.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                if let aiResponse = result.ai_response, !aiResponse.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Réponse IA")
                            .font(.subheadline.weight(.semibold))
                        Text(aiResponse)
                            .font(.system(.callout, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                if let checks = result.validation?.checks, !checks.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Checks")
                            .font(.subheadline.weight(.semibold))
                        ForEach(checks) { check in
                            SelfUpdateCheckRow(check: check)
                        }
                    }
                }
            }
            .padding(16)
            .premiumSurface(cornerRadius: 18, highlighted: result.status == "ok")
        }
    }
}

struct SelfUpdatePathField: View {
    let title: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            TextField(title, text: $text)
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
        }
    }
}

struct SelfUpdateCheckRow: View {
    let check: AppState.SelfUpdateCheck
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                expanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: check.ok == true ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(check.ok == true ? UIPalette.success : UIPalette.error)
                    Text(check.command)
                        .font(.system(.caption, design: .monospaced))
                        .lineLimit(1)
                    Spacer()
                    Text("code \(check.returncode ?? 0)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)

            if expanded, let output = check.output, !output.isEmpty {
                Text(output)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.black.opacity(0.18))
                    )
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.white.opacity(0.04))
        )
    }
}

struct LocalModelsView: View {
    @Bindable var appState: AppState

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    UIPalette.background,
                    UIPalette.surface.opacity(0.90)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Modèles locaux")
                            .font(.largeTitle.weight(.semibold))
                        Text("Espace de visibilité pour les modèles présents sur la machine.")
                            .foregroundStyle(.secondary)
                    }

                    HStack(spacing: 12) {
                        Label(
                            appState.diagnosticsSummary?.heretic?.installed == true ? "Heretic détecté" : "Heretic indisponible",
                            systemImage: appState.diagnosticsSummary?.heretic?.installed == true ? "checkmark.seal.fill" : "xmark.seal"
                        )
                        .font(.headline)
                        .foregroundStyle(appState.diagnosticsSummary?.heretic?.installed == true ? .blue : .secondary)

                        if let version = appState.diagnosticsSummary?.heretic?.version, !version.isEmpty {
                            Text("v\(version)")
                                .font(.caption.weight(.semibold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(
                                    Capsule(style: .continuous)
                                        .fill(Color.white.opacity(0.08))
                                )
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .premiumSurface(cornerRadius: 20)

                    if appState.localExperimentalModels.isEmpty {
                        RoundedRectangle(cornerRadius: 24, style: .continuous)
                            .fill(Color.white.opacity(0.06))
                            .frame(height: 160)
                            .overlay(
                                RoundedRectangle(cornerRadius: 24, style: .continuous)
                                    .strokeBorder(Color.white.opacity(0.07), lineWidth: 0.9)
                            )
                            .overlay {
                                VStack(spacing: 10) {
                                    Image(systemName: "sparkles.rectangle.stack")
                                        .font(.system(size: 28))
                                        .foregroundStyle(.secondary)
                                    Text("Aucun modèle local expérimental détecté")
                                        .foregroundStyle(.secondary)
                                }
                            }
                    } else {
                        ForEach(appState.localExperimentalModels, id: \.self) { model in
                            HStack {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(model)
                                        .font(.headline)
                                    Text("Détecté localement")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(.blue)
                            }
                            .padding(18)
                            .premiumSurface(cornerRadius: 20)
                        }
                    }

                    Text("Je n’ajoute pas ici d’outil de contournement des garde-fous. En revanche, cette vue peut servir de base propre pour gérer les modèles locaux et leurs statuts.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(28)
            }
        }
    }
}
