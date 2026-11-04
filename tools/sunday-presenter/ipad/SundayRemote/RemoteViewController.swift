import UIKit
import WebKit

final class RemoteViewController: UIViewController, UITextFieldDelegate {
    let client = RemoteClient()
    private var viewer: RemoteVideoView!
    private let computers = UIButton(type: .system)
    private let status = UILabel()
    private let code = UITextField()
    private let pair = UIButton(type: .system)
    private let previous = UIButton(type: .system)
    private let nextButton = UIButton(type: .system)
    private let fullscreen = UIButton(type: .system)
    private let forget = UIButton(type: .system)
    private let observing = UISwitch()
    private let connection = UIStackView()
    private let pairing = UIStackView()
    private let cover = UILabel()
    private var expanded = false
    private var foreground = false
    private var lastComputers = ""

    override var prefersStatusBarHidden: Bool { expanded }
    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }
    override func viewDidLoad() {
        super.viewDidLoad(); view.backgroundColor = UIColor(red: 0.035, green: 0.055, blue: 0.09, alpha: 1)
        guard let resources = Bundle.main.resourceURL else { return }
        viewer = RemoteVideoView(resources: resources)
        viewer.webView.isOpaque = false; viewer.webView.backgroundColor = .black
        viewer.webView.scrollView.isScrollEnabled = false
        viewer.onMessage = { [weak self] value in self?.client.videoMessage(value) }
        client.applyVideo = { [weak self] value in self?.viewer.apply(value) }
        client.stopVideo = { [weak self] text in self?.viewer.stop(text) }
        client.onChange = { [weak self] in self?.render() }

        computers.showsMenuAsPrimaryAction = true
        computers.contentHorizontalAlignment = .leading
        computers.accessibilityLabel = "選擇主電腦"
        status.font = .preferredFont(forTextStyle: .footnote); status.textColor = .lightGray
        status.numberOfLines = 2; status.accessibilityIdentifier = "connection-status"
        code.borderStyle = .roundedRect; code.backgroundColor = .secondarySystemBackground
        code.placeholder = "主電腦六位數字"; code.keyboardType = .numberPad
        code.textAlignment = .center; code.delegate = self; code.accessibilityLabel = "六位數配對碼"
        code.addTarget(self, action: #selector(codeChanged), for: .editingChanged)
        configure(pair, "第一次連線", #selector(connect))
        configure(previous, "← 上一頁", #selector(back))
        configure(nextButton, "下一頁 →", #selector(forward))
        configure(fullscreen, "全螢幕", #selector(toggleFullscreen))
        configure(forget, "重新配對", #selector(resetPair))
        observing.isOn = true; observing.accessibilityLabel = "只看畫面，停用翻頁"
        observing.addTarget(self, action: #selector(observeChanged), for: .valueChanged)
        let observeLabel = UILabel(); observeLabel.text = "只看畫面"; observeLabel.textColor = .white
        observeLabel.font = .preferredFont(forTextStyle: .footnote)
        let options = row([observeLabel, observing, UIView(), forget])
        connection.axis = .vertical; connection.spacing = 4
        connection.addArrangedSubview(computers); connection.addArrangedSubview(status)
        connection.addArrangedSubview(options)
        pairing.axis = .horizontal; pairing.spacing = 8
        pairing.addArrangedSubview(code); pairing.addArrangedSubview(pair)
        let buttons = row([previous, nextButton, fullscreen]); buttons.distribution = .fillEqually
        let footer = UIStackView(arrangedSubviews: [connection, pairing, buttons])
        footer.axis = .vertical; footer.spacing = 6; footer.isLayoutMarginsRelativeArrangement = true
        footer.layoutMargins = UIEdgeInsets(top: 6, left: 12, bottom: 8, right: 12)
        footer.backgroundColor = UIColor(white: 0.1, alpha: 1)
        let stack = UIStackView(arrangedSubviews: [viewer.webView, footer]); stack.axis = .vertical
        stack.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(stack)
        viewer.webView.setContentHuggingPriority(.defaultLow, for: .vertical)
        viewer.webView.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        footer.setContentHuggingPriority(.required, for: .vertical)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            stack.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            stack.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
            code.widthAnchor.constraint(greaterThanOrEqualToConstant: 140)
        ])
        cover.backgroundColor = view.backgroundColor; cover.textColor = .white; cover.textAlignment = .center
        cover.text = "畫面已暫停"; cover.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(cover)
        NSLayoutConstraint.activate([cover.topAnchor.constraint(equalTo: view.topAnchor), cover.bottomAnchor.constraint(equalTo: view.bottomAnchor),
                                     cover.leadingAnchor.constraint(equalTo: view.leadingAnchor), cover.trailingAnchor.constraint(equalTo: view.trailingAnchor)])
        render()
    }
    private func row(_ views: [UIView]) -> UIStackView {
        let stack = UIStackView(arrangedSubviews: views); stack.axis = .horizontal; stack.spacing = 8; stack.alignment = .center; return stack
    }
    private func configure(_ button: UIButton, _ title: String, _ action: Selector) {
        var config = UIButton.Configuration.tinted(); config.title = title
        config.baseForegroundColor = .white; config.baseBackgroundColor = .systemBlue
        button.configuration = config; button.addTarget(self, action: action, for: .touchUpInside)
        button.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true
    }
    func resume() {
        loadViewIfNeeded(); foreground = true; cover.isHidden = true
        client.start(); render()
    }
    func pause() {
        foreground = false; client.suspend(); UIApplication.shared.isIdleTimerDisabled = false
        cover.isHidden = false; expanded = false; setNeedsStatusBarAppearanceUpdate(); render()
    }
    private func render() {
        guard isViewLoaded else { return }
        status.text = client.status
        let list = client.computers
        let menuKey = list.map { $0.id }.joined(separator: "|") + (client.selectedID ?? "")
        if menuKey != lastComputers {
            lastComputers = menuKey
            computers.menu = UIMenu(children: list.map { computer in
                UIAction(title: computer.name, state: computer.id == client.selectedID ? .on : .off) { [weak self] _ in
                    self?.view.endEditing(true); self?.client.select(computer.id)
                }
            })
        }
        computers.setTitle(list.first(where: { $0.id == client.selectedID })?.name ?? "尋找主電腦…", for: .normal)
        pairing.isHidden = client.paired || expanded
        if client.paired { code.text = ""; code.resignFirstResponder() }
        connection.isHidden = expanded
        // Unpairing/loss must expose connection and pairing recovery even in fullscreen.
        if expanded && !client.paired { expanded = false; connection.isHidden = false; pairing.isHidden = false; setNeedsStatusBarAppearanceUpdate() }
        pair.isEnabled = client.canPair && RemoteScope.validCode(code.text ?? "")
        code.isEnabled = !client.pairingBusy; previous.isEnabled = client.canFlip; nextButton.isEnabled = client.canFlip
        observing.isOn = client.scope.observing
        var fullscreenConfig = fullscreen.configuration
        fullscreenConfig?.title = expanded ? "顯示連線" : "全螢幕"
        fullscreen.configuration = fullscreenConfig
        UIApplication.shared.isIdleTimerDisabled = foreground && client.videoReceiving
    }
    @objc private func codeChanged() { render() }
    func textField(_ textField: UITextField, shouldChangeCharactersIn range: NSRange, replacementString string: String) -> Bool {
        guard let old = textField.text, let r = Range(range, in: old) else { return false }
        let next = old.replacingCharacters(in: r, with: string)
        return next.utf8.count <= 6 && next.utf8.allSatisfy { (48...57).contains($0) }
    }
    @objc private func connect() { view.endEditing(true); client.pair(code.text ?? "") }
    @objc private func back() { client.flip("Left") }
    @objc private func forward() { client.flip("Right") }
    @objc private func observeChanged() { client.observe(observing.isOn) }
    @objc private func toggleFullscreen() {
        view.endEditing(true); expanded.toggle(); setNeedsStatusBarAppearanceUpdate(); render()
    }
    @objc private func resetPair() {
        let alert = UIAlertController(title: "重新配對這台主電腦？", message: "這台 iPad 保存的配對將移除；其他裝置不受影響。", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        alert.addAction(UIAlertAction(title: "重新配對", style: .destructive) { [weak self] _ in self?.client.forget() })
        present(alert, animated: true)
    }
    override var keyCommands: [UIKeyCommand]? {
        [UIKeyCommand(input: UIKeyCommand.inputLeftArrow, modifierFlags: [], action: #selector(back)),
         UIKeyCommand(input: UIKeyCommand.inputRightArrow, modifierFlags: [], action: #selector(forward))]
    }
    override var canBecomeFirstResponder: Bool { true }
}
