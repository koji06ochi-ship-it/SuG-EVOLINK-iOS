import Foundation
import UIKit
import WebKit

/// JSON is passed directly from JS to the native Files picker; no blob navigation or network upload.
@MainActor
final class EvolinkFileExport: NSObject, UIDocumentPickerDelegate {
    private weak var webView: WKWebView?
    private var activeID: String?
    private var temporaryFolder: URL?

    func export(_ body: [String: String], from webView: WKWebView) {
        guard let id = body["id"], id.count <= 100 else { return }
        guard activeID == nil else { reply(id, "failed", "先の書出し操作を終了してください", on: webView); return }
        guard let json = body["json"], let data = json.data(using: .utf8), data.count <= 10 * 1024 * 1024,
              (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else {
            reply(id, "failed", "バックアップJSONの形式または容量が不正です", on: webView); return
        }
        guard var presenter = webView.window?.rootViewController else {
            reply(id, "failed", "保存画面を開けませんでした", on: webView); return
        }
        while let shown = presenter.presentedViewController { presenter = shown }
        guard !presenter.isBeingDismissed, !(presenter is UIDocumentPickerViewController) else {
            reply(id, "failed", "他のファイル操作が進行中です", on: webView); return
        }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("evolink-export-" + UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            // Never use a JS path as a filesystem path. Fixed filename, unique temporary directory.
            let file = folder.appendingPathComponent("SUG_EVOLINK_PRIVATE_BACKUP.json")
            try data.write(to: file, options: [.atomic, .completeFileProtection])
            self.webView = webView; activeID = id; temporaryFolder = folder
            let picker = UIDocumentPickerViewController(forExporting: [file], asCopy: true)
            picker.delegate = self
            presenter.present(picker, animated: true)
        } catch {
            try? FileManager.default.removeItem(at: folder)
            reply(id, "failed", "一時バックアップファイルを作成できませんでした", on: webView)
        }
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        complete(urls.isEmpty ? "cancelled" : "exported")
    }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { complete("cancelled") }
    private func complete(_ status: String) {
        if let id = activeID, let view = webView { reply(id, status, "", on: view) }
        if let folder = temporaryFolder { try? FileManager.default.removeItem(at: folder) }
        activeID = nil; temporaryFolder = nil; webView = nil
    }
    private func reply(_ id: String, _ status: String, _ message: String, on view: WKWebView) {
        view.callAsyncJavaScript("return window.SuGEvolinkNativeFiles?.finish(id, status, message);",
            arguments: ["id":id, "status":status, "message":message], in: nil, in: .page, completionHandler: nil)
    }
}
