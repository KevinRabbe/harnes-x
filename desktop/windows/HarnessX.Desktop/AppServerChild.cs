using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace HarnessX.Desktop;

internal sealed class DesktopHandshake
{
    [JsonPropertyName("schema_version")]
    public string SchemaVersion { get; init; } = string.Empty;

    [JsonPropertyName("base_url")]
    public string BaseUrl { get; init; } = string.Empty;

    [JsonPropertyName("ui_url")]
    public string UiUrl { get; init; } = string.Empty;

    [JsonPropertyName("ui_bootstrap_url")]
    public string UiBootstrapUrl { get; init; } = string.Empty;

    [JsonPropertyName("pid")]
    public int Pid { get; init; }
}

internal sealed class AppServerChild : IAsyncDisposable
{
    private readonly Process _process;
    private readonly Task<string> _stderrTask;
    private bool _stopped;

    private AppServerChild(
        Process process,
        Task<string> stderrTask,
        Uri baseUri,
        Uri uiUri,
        Uri bootstrapUri)
    {
        _process = process;
        _stderrTask = stderrTask;
        BaseUri = baseUri;
        UiUri = uiUri;
        BootstrapUri = bootstrapUri;
    }

    public Uri BaseUri { get; }

    public Uri UiUri { get; }

    public Uri BootstrapUri { get; }

    public static async Task<AppServerChild> StartAsync(
        string appServerExecutable,
        string appServerRoot,
        CancellationToken cancellationToken = default)
    {
        var executable = Path.GetFullPath(appServerExecutable);
        if (!File.Exists(executable))
        {
            throw new FileNotFoundException(
                "Harness X App Server executable does not exist.",
                executable);
        }

        Directory.CreateDirectory(appServerRoot);
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("--root");
        startInfo.ArgumentList.Add(appServerRoot);
        startInfo.ArgumentList.Add("--host");
        startInfo.ArgumentList.Add("127.0.0.1");
        startInfo.ArgumentList.Add("--port");
        startInfo.ArgumentList.Add("0");
        startInfo.ArgumentList.Add("--desktop-host");

        var process = new Process
        {
            StartInfo = startInfo,
            EnableRaisingEvents = true,
        };
        if (!process.Start())
        {
            process.Dispose();
            throw new InvalidOperationException("Harness X App Server process did not start.");
        }

        var stderrTask = process.StandardError.ReadToEndAsync();
        try
        {
            var lineTask = process.StandardOutput.ReadLineAsync();
            var exitTask = process.WaitForExitAsync();
            var timeoutTask = Task.Delay(TimeSpan.FromSeconds(20), cancellationToken);
            var completed = await Task.WhenAny(lineTask, exitTask, timeoutTask).ConfigureAwait(false);

            if (completed == timeoutTask)
            {
                cancellationToken.ThrowIfCancellationRequested();
                throw new TimeoutException("Timed out waiting for Harness X desktop startup handshake.");
            }
            if (completed == exitTask && !lineTask.IsCompleted)
            {
                throw new InvalidOperationException(
                    $"Harness X App Server exited before startup handshake: {(await stderrTask.ConfigureAwait(false)).Trim()}");
            }

            var line = await lineTask.ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(line))
            {
                var stderr = process.HasExited ? await stderrTask.ConfigureAwait(false) : string.Empty;
                throw new InvalidOperationException(
                    $"Harness X App Server returned an empty startup handshake. {stderr}".Trim());
            }

            DesktopHandshake? handshake;
            try
            {
                handshake = JsonSerializer.Deserialize<DesktopHandshake>(line);
            }
            catch (JsonException exc)
            {
                throw new InvalidOperationException("Harness X App Server returned invalid startup JSON.", exc);
            }
            if (handshake is null || handshake.SchemaVersion != "app-server-desktop-start-v1")
            {
                throw new InvalidOperationException("Harness X App Server returned an unsupported desktop handshake.");
            }
            // On Windows, pip's console-script .exe may hand execution to a Python process.
            // The redirected stdio pipe is the ownership/authentication boundary, so the server
            // PID can legitimately differ from the launcher PID started by this process.
            if (handshake.Pid <= 0)
            {
                throw new InvalidOperationException("Harness X App Server returned an invalid server PID.");
            }

            var baseUri = RequireLoopbackHttp(handshake.BaseUrl, "base_url", allowFragment: false);
            var uiUri = RequireLoopbackHttp(handshake.UiUrl, "ui_url", allowFragment: false);
            var bootstrapUri = RequireLoopbackHttp(
                handshake.UiBootstrapUrl,
                "ui_bootstrap_url",
                allowFragment: true);
            var origin = baseUri.GetLeftPart(UriPartial.Authority);
            if (!string.Equals(uiUri.GetLeftPart(UriPartial.Authority), origin, StringComparison.OrdinalIgnoreCase)
                || !string.Equals(bootstrapUri.GetLeftPart(UriPartial.Authority), origin, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Harness X desktop handshake URLs do not share one loopback origin.");
            }
            if (!string.IsNullOrEmpty(bootstrapUri.Query)
                || !bootstrapUri.Fragment.StartsWith("#bootstrap=", StringComparison.Ordinal)
                || bootstrapUri.Fragment.Length <= "#bootstrap=".Length)
            {
                throw new InvalidOperationException("Harness X desktop bootstrap URL is missing its one-time fragment ticket.");
            }

            return new AppServerChild(process, stderrTask, baseUri, uiUri, bootstrapUri);
        }
        catch
        {
            await TerminateAsync(process).ConfigureAwait(false);
            process.Dispose();
            throw;
        }
    }

    public async Task StopAsync()
    {
        if (_stopped)
        {
            return;
        }
        _stopped = true;

        if (!_process.HasExited)
        {
            try
            {
                _process.StandardInput.Close();
            }
            catch (InvalidOperationException)
            {
            }

            using var shutdown = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            try
            {
                await _process.WaitForExitAsync(shutdown.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                await TerminateAsync(_process).ConfigureAwait(false);
            }
        }

        if (_process.HasExited)
        {
            await _stderrTask.ConfigureAwait(false);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        _process.Dispose();
    }

    private static Uri RequireLoopbackHttp(string value, string field, bool allowFragment)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri)
            || uri.Scheme != Uri.UriSchemeHttp
            || !string.Equals(uri.Host, "127.0.0.1", StringComparison.Ordinal)
            || uri.Port <= 0
            || !string.IsNullOrEmpty(uri.UserInfo)
            || (!allowFragment && !string.IsNullOrEmpty(uri.Fragment)))
        {
            throw new InvalidOperationException($"Harness X desktop handshake {field} is not a valid loopback HTTP URL.");
        }
        return uri;
    }

    private static async Task TerminateAsync(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
        }

        try
        {
            await process.WaitForExitAsync().ConfigureAwait(false);
        }
        catch (InvalidOperationException)
        {
        }
    }
}
