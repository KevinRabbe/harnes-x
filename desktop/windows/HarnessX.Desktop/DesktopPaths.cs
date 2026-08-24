namespace HarnessX.Desktop;

internal sealed record DesktopPaths(string AppServerRoot, string WebViewUserDataRoot)
{
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
        Directory.CreateDirectory(appServer);
        Directory.CreateDirectory(webView);
        return new DesktopPaths(appServer, webView);
    }
}
