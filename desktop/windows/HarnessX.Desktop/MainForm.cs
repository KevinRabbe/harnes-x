using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace HarnessX.Desktop;

internal sealed class MainForm : Form
{
    private readonly Label _status;
    private readonly WebView2 _webView;
    private DesktopPaths? _paths;
    private AppServerChild? _server;
    private bool _started;
    private bool _shutdownStarted;
    private bool _shutdownComplete;
    private bool _windowStateSaved;

    public MainForm()
    {
        Text = "Harness X";
        Width = 1200;
        Height = 820;
        MinimumSize = new Size(820, 560);
        StartPosition = FormStartPosition.CenterScreen;

        _webView = new WebView2
        {
            Dock = DockStyle.Fill,
            Visible = false,
        };
        _status = new Label
        {
            Dock = DockStyle.Fill,
            Text = "Starting Harness X…",
            TextAlign = ContentAlignment.MiddleCenter,
            AutoSize = false,
        };

        Controls.Add(_webView);
        Controls.Add(_status);
    }

    protected override void OnLoad(EventArgs e)
    {
        base.OnLoad(e);
        try
        {
            _paths = DesktopPaths.Create();
            DesktopWindowStateStore.TryApply(this, _paths.WindowStatePath);
        }
        catch
        {
            // App Server startup remains responsible for presenting a fatal local-path failure.
            // Window-state recovery is convenience-only and must always fail back to defaults.
            _paths = null;
        }
    }

    protected override async void OnShown(EventArgs e)
    {
        base.OnShown(e);
        if (_started)
        {
            return;
        }
        _started = true;

        try
        {
            var paths = _paths ?? DesktopPaths.Create();
            _paths = paths;
            var appServerExecutable = DesktopRuntimeLocator.ResolveOrPrompt(this, paths);
            _server = await AppServerChild.StartAsync(appServerExecutable, paths.AppServerRoot);
            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: paths.WebViewUserDataRoot,
                options: null);
            await _webView.EnsureCoreWebView2Async(environment);
            ConfigureWebView(_server);
            _status.Visible = false;
            _webView.Visible = true;
            _webView.Source = _server.BootstrapUri;
        }
        catch (OperationCanceledException)
        {
            Close();
        }
        catch (Exception exc)
        {
            MessageBox.Show(
                this,
                $"Harness X could not start.\n\n{exc.Message}",
                "Harness X startup failed",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            Close();
        }
    }

    protected override async void OnFormClosing(FormClosingEventArgs e)
    {
        SaveWindowStateOnce();
        if (_shutdownComplete || _server is null)
        {
            base.OnFormClosing(e);
            return;
        }
        if (_shutdownStarted)
        {
            e.Cancel = true;
            return;
        }

        e.Cancel = true;
        _shutdownStarted = true;
        _webView.Visible = false;
        _status.Text = "Closing Harness X…";
        _status.Visible = true;
        Enabled = false;

        try
        {
            await _server.StopAsync();
        }
        finally
        {
            _shutdownComplete = true;
            Enabled = true;
            Close();
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _webView.Dispose();
        }
        base.Dispose(disposing);
    }

    private void SaveWindowStateOnce()
    {
        if (_windowStateSaved || _paths is null)
        {
            return;
        }
        _windowStateSaved = true;
        var maximized = WindowState == FormWindowState.Maximized;
        var bounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
        DesktopWindowStateStore.TrySave(
            _paths.WindowStatePath,
            bounds,
            maximized,
            MinimumSize);
    }

    private void ConfigureWebView(AppServerChild server)
    {
        var core = _webView.CoreWebView2
            ?? throw new InvalidOperationException("WebView2 did not initialize.");
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreBrowserAcceleratorKeysEnabled = true;

        core.NavigationStarting += (_, args) =>
        {
            if (!DesktopUriPolicy.IsAllowedNavigation(args.Uri, server.BaseUri))
            {
                args.Cancel = true;
                OpenExternal(args.Uri);
            }
        };
        core.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            if (DesktopUriPolicy.IsAllowedNavigation(args.Uri, server.BaseUri)
                && Uri.TryCreate(args.Uri, UriKind.Absolute, out var local))
            {
                _webView.Source = local;
                return;
            }
            OpenExternal(args.Uri);
        };
        core.ProcessFailed += (_, args) =>
        {
            _webView.Visible = false;
            _status.Text = (
                $"Harness X WebView2 process failed: {args.ProcessFailedKind}.\n\n"
                + "Close and reopen Harness X to reconstruct the workspace from durable local state."
            );
            _status.Visible = true;
        };
    }

    private static void OpenExternal(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return;
        }
        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
        catch
        {
        }
    }
}
