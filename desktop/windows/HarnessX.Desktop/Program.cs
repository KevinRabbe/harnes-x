namespace HarnessX.Desktop;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--smoke-test")
        {
            return RunSmokeTestAsync().GetAwaiter().GetResult();
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
        return 0;
    }

    private static async Task<int> RunSmokeTestAsync()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"harness-x-desktop-smoke-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(root);
            var paths = new DesktopPaths(
                root,
                Path.Combine(root, "AppServer"),
                Path.Combine(root, "WebView2"),
                Path.Combine(root, "app-server-executable.txt"));

            var minimumSize = new Size(820, 560);
            var savedBounds = new Rectangle(120, 140, 1100, 720);
            if (!DesktopWindowStateStore.TrySave(
                    paths.WindowStatePath,
                    savedBounds,
                    maximized: true,
                    minimumSize))
            {
                return 7;
            }
            var windowState = DesktopWindowStateStore.TryLoad(paths.WindowStatePath, minimumSize);
            if (windowState is null
                || windowState.SchemaVersion != 1
                || windowState.X != savedBounds.X
                || windowState.Y != savedBounds.Y
                || windowState.Width != savedBounds.Width
                || windowState.Height != savedBounds.Height
                || !windowState.Maximized)
            {
                return 8;
            }
            var windowStateJson = File.ReadAllText(paths.WindowStatePath);
            foreach (var forbidden in new[]
                     {
                         "token", "bearer", "project_id", "chat_id", "execution_id",
                         "workspace", "task", "evidence", "model_profile",
                     })
            {
                if (windowStateJson.Contains(forbidden, StringComparison.OrdinalIgnoreCase))
                {
                    return 9;
                }
            }
            if (!DesktopWindowStateStore.IsVisibleOnAnyScreen(
                    savedBounds,
                    new[] { new Rectangle(0, 0, 1920, 1080) })
                || DesktopWindowStateStore.IsVisibleOnAnyScreen(
                    new Rectangle(50_000, 50_000, 1000, 700),
                    new[] { new Rectangle(0, 0, 1920, 1080) }))
            {
                return 10;
            }
            File.WriteAllText(paths.WindowStatePath, "{not-json");
            if (DesktopWindowStateStore.TryLoad(paths.WindowStatePath, minimumSize) is not null)
            {
                return 11;
            }

            var appServerExecutable = DesktopRuntimeLocator.TryResolve(paths);
            if (appServerExecutable is null)
            {
                return 4;
            }

            if (string.Equals(
                    Environment.GetEnvironmentVariable("HARNESS_X_DESKTOP_SMOKE_REQUIRE_ADJACENT"),
                    "1",
                    StringComparison.Ordinal))
            {
                var adjacent = Path.GetFullPath(
                    Path.Combine(AppContext.BaseDirectory, "harness-x-app-server.exe"));
                if (!string.Equals(
                        Path.GetFullPath(appServerExecutable),
                        adjacent,
                        StringComparison.OrdinalIgnoreCase))
                {
                    return 12;
                }
            }

            if (string.Equals(
                    Path.GetFileName(appServerExecutable),
                    "harness-x-app-server.exe",
                    StringComparison.OrdinalIgnoreCase))
            {
                if (!DesktopRuntimeLocator.TryRemember(paths, appServerExecutable))
                {
                    return 5;
                }

                var configured = Environment.GetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE");
                var pathValue = Environment.GetEnvironmentVariable("PATH");
                try
                {
                    Environment.SetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE", null);
                    Environment.SetEnvironmentVariable("PATH", string.Empty);
                    var remembered = DesktopRuntimeLocator.TryResolve(paths);
                    if (!string.Equals(remembered, appServerExecutable, StringComparison.OrdinalIgnoreCase))
                    {
                        return 6;
                    }
                }
                finally
                {
                    Environment.SetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE", configured);
                    Environment.SetEnvironmentVariable("PATH", pathValue);
                }
            }

            await using var server = await AppServerChild.StartAsync(
                appServerExecutable,
                paths.AppServerRoot);
            if (!DesktopUriPolicy.IsAllowedNavigation(server.BootstrapUri.AbsoluteUri, server.BaseUri))
            {
                return 2;
            }
            if (DesktopUriPolicy.IsAllowedNavigation("https://example.com/", server.BaseUri))
            {
                return 3;
            }

            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            using var uiResponse = await http.GetAsync(server.UiUri);
            if (!uiResponse.IsSuccessStatusCode)
            {
                return 13;
            }
            var uiText = await uiResponse.Content.ReadAsStringAsync();
            if (!uiText.Contains("<h1>Projects &amp; Chats</h1>", StringComparison.Ordinal))
            {
                return 14;
            }

            var observatoryUri = new Uri(server.BaseUri, "/ui/improvement_observatory_bridge.js");
            using var observatoryResponse = await http.GetAsync(observatoryUri);
            if (!observatoryResponse.IsSuccessStatusCode)
            {
                return 15;
            }
            var observatoryText = await observatoryResponse.Content.ReadAsStringAsync();
            if (!observatoryText.Contains("Improvement Observatory", StringComparison.Ordinal))
            {
                return 16;
            }

            await server.StopAsync();
            return 0;
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine(exc);
            return 1;
        }
        finally
        {
            try
            {
                Directory.Delete(root, recursive: true);
            }
            catch
            {
            }
        }
    }
}
