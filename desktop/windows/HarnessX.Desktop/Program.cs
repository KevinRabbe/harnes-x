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
            await using var server = await AppServerChild.StartAsync(root);
            if (!DesktopUriPolicy.IsAllowedNavigation(server.BootstrapUri.AbsoluteUri, server.BaseUri))
            {
                return 2;
            }
            if (DesktopUriPolicy.IsAllowedNavigation("https://example.com/", server.BaseUri))
            {
                return 3;
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
