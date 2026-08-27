namespace HarnessX.Desktop;

internal sealed record DesktopPaths(
    string Root,
    string AppServerRoot,
    string WebViewUserDataRoot,
    string AppServerExecutablePathFile)
{
    public string WindowStatePath => Path.Combine(Root, "window-state-v1.json");

    public static DesktopPaths Create()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrWhiteSpace(localAppData))
        {
            throw new InvalidOperationException("Windows local application-data directory is unavailable.");
        }

        var root = Path.Combine(localAppData, "Harness X");
        var appServer = Path.Combine(root, "AppServer");
        var webView = Path.Combine(root, "WebView2");
        Directory.CreateDirectory(root);
        Directory.CreateDirectory(appServer);
        Directory.CreateDirectory(webView);
        return new DesktopPaths(
            root,
            appServer,
            webView,
            Path.Combine(root, "app-server-executable.txt"));
    }
}
