// Split a scanned PDF into one JPEG per page: split_pdf.swift <in.pdf> <out-dir>
// Prints the page count. PDFKit ships with macOS, so no Python PDF dependency.
import AppKit
import PDFKit

let args = CommandLine.arguments
guard args.count == 3, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
  FileHandle.standardError.write("usage: split_pdf.swift <in.pdf> <out-dir>\n".data(using: .utf8)!)
  exit(2)
}
for index in 0..<doc.pageCount {
  guard let page = doc.page(at: index) else { continue }
  let box = page.bounds(for: .mediaBox)
  let scale = 3000.0 / max(box.width, box.height)
  let image = page.thumbnail(of: NSSize(width: box.width * scale, height: box.height * scale), for: .mediaBox)
  guard let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
        let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.85]) else {
    FileHandle.standardError.write("page \(index + 1) could not be rendered\n".data(using: .utf8)!)
    exit(1)
  }
  try jpeg.write(to: URL(fileURLWithPath: "\(args[2])/page-\(String(format: "%03d", index + 1)).jpg"))
}
print(doc.pageCount)
