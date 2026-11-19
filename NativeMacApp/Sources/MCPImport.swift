import AppKit
import Darwin
import SwiftUI
import UniformTypeIdentifiers

// Create the new file owner-only before exposing it under its permanent name.
func writePrivateFile(_ data: Data, to destination: URL) throws {
    let folder = destination.deletingLastPathComponent()
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                           attributes: [.posixPermissions: 0o700])
    var template = Array(folder.appending(path: ".macagent-XXXXXX").path.utf8CString)
    let fd = mkstemp(&template)
    guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
    let temporary = URL(fileURLWithFileSystemRepresentation: template, isDirectory: false, relativeTo: nil)
    defer { try? FileManager.default.removeItem(at: temporary) }
    let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    try file.write(contentsOf: data)
    try file.close()
    guard rename(temporary.path, destination.path) == 0 else {
        throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
}

struct ClaudeMCPImport: Identifiable {
    let id = UUID()
    let filename: String
    let data: Data
    let previews: [String]

    enum ImportError: LocalizedError {
        case malformed
        case unsupported(String)
        case tooLarge

        var errorDescription: String? {
            switch self {
            case .malformed: return "Invalid mcpServers configuration."
            case .unsupported(let name): return "Unsupported or invalid MCP transport for “\(name)”."
            case .tooLarge: return "The MCP configuration exceeds 2 MB."
            }
        }
    }

    init(file: URL) throws {
        filename = file.lastPathComponent
        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        let limit = 2 * 1024 * 1024
        let raw = try input.read(upToCount: limit + 1) ?? Data()
        guard raw.count <= limit else { throw ImportError.tooLarge }
        guard let root = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
              var servers = root["mcpServers"] as? [String: [String: Any]] else {
            throw ImportError.malformed
        }
        var summary: [String] = []
        for name in servers.keys.sorted() {
            guard !name.isEmpty, var entry = servers[name] else { throw ImportError.malformed }
            let transport = entry["type"] as? String ?? (entry["url"] == nil ? "stdio" : "http")
            switch transport {
            case "stdio":
                guard let command = entry["command"] as? String, !command.isEmpty,
                      entry["args"] == nil || entry["args"] is [String] else { throw ImportError.unsupported(name) }
                let args = entry["args"] as? [String] ?? []
                let directory = file.deletingLastPathComponent()
                if let cwd = entry["cwd"] as? String, !cwd.isEmpty {
                    if !cwd.hasPrefix("/") && !cwd.hasPrefix("$") && !cwd.hasPrefix("~") {
                        entry["cwd"] = directory.appending(path: cwd).standardizedFileURL.path
                    }
                } else {
                    entry["cwd"] = directory.path
                }
                servers[name] = entry
                let invocation = ([command] + args).map { String(reflecting: $0) }.joined(separator: " ")
                summary.append("\(name) · stdio\n\(invocation)")
            case "http", "streamable-http", "sse":
                guard let url = entry["url"] as? String, !url.isEmpty else { throw ImportError.unsupported(name) }
                entry["type"] = transport
                servers[name] = entry
                // Headers, query parameters and URL credentials stay out of the preview.
                let host = URLComponents(string: url)?.host ?? "(configured URL)"
                summary.append("\(name) · \(transport) · \(host)")
            default:
                throw ImportError.unsupported(name)
            }
        }
        previews = summary
        data = try JSONSerialization.data(withJSONObject: ["mcpServers": servers], options: [.prettyPrinted, .sortedKeys])
    }
}

struct MCPImportControls: View {
    @Bindable var appState: AppState
    @State private var candidate: ClaudeMCPImport?
    @State private var confirmRemoval = false
    @State private var message = ""
    @State private var hasConfiguration = false

    private var destination: URL { appState.conversationsDirectory.appending(path: "mcp.json") }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button(appState.l("Importer une configuration…", "Import configuration…")) {
                    let panel = NSOpenPanel()
                    panel.allowedContentTypes = [.json]
                    panel.showsHiddenFiles = true
                    guard panel.runModal() == .OK, let url = panel.url else { return }
                    do { candidate = try ClaudeMCPImport(file: url) }
                    catch { message = error.localizedDescription }
                }
                if hasConfiguration {
                    Button(appState.l("Retirer", "Remove")) { confirmRemoval = true }
                }
            }
            if !message.isEmpty {
                Text(message).font(.caption).foregroundStyle(.secondary)
            }
        }
        .onAppear { hasConfiguration = FileManager.default.fileExists(atPath: destination.path) }
        .sheet(item: $candidate) { item in
            VStack(alignment: .leading, spacing: 16) {
                Text(appState.l("Importer les serveurs MCP", "Import MCP servers")).font(.title2)
                Text(item.filename).font(.headline)
                Text(appState.l(
                    "Cette liste remplacera les connecteurs importés. Au prochain démarrage du backend, les commandes locales s’exécuteront avec les droits de ton compte et les serveurs distants recevront leurs en-têtes configurés.",
                    "This replaces the imported connectors. On the next backend start, local commands run with your account’s permissions and remote servers receive their configured headers."
                ))
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        if item.previews.isEmpty {
                            Text(appState.l("Aucun serveur", "No servers"))
                        }
                        ForEach(Array(item.previews.enumerated()), id: \.offset) { _, preview in
                            Text(verbatim: preview).textSelection(.enabled)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack {
                    Spacer()
                    Button(appState.l("Annuler", "Cancel")) { candidate = nil }.keyboardShortcut(.cancelAction)
                    Button(appState.l("Importer", "Import")) {
                        do {
                            try writePrivateFile(item.data, to: destination)
                            hasConfiguration = true
                            message = restartMessage
                        } catch { message = error.localizedDescription }
                        candidate = nil
                    }
                }
            }.padding(24).frame(width: 540, height: 390)
        }
        .confirmationDialog(appState.l("Retirer les connecteurs importés ?", "Remove imported connectors?"), isPresented: $confirmRemoval) {
            Button(appState.l("Retirer", "Remove"), role: .destructive) {
                do {
                    try FileManager.default.removeItem(at: destination)
                    hasConfiguration = false
                    message = restartMessage
                } catch { message = error.localizedDescription }
            }
        }
    }

    private var restartMessage: String {
        appState.l("Relance le backend pour appliquer ce changement. Dans l’app avec backend embarqué, quitte puis rouvre Mac Agent OS.",
                   "Restart the backend to apply this change. With the bundled backend, quit and reopen Mac Agent OS.")
    }
}
