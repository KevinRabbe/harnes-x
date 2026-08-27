using System.Text.Json;
using System.Text.Json.Serialization;

namespace HarnessX.Desktop;

internal sealed record DesktopWindowState(
    [property: JsonPropertyName("schema_version")] int SchemaVersion,
    [property: JsonPropertyName("x")] int X,
    [property: JsonPropertyName("y")] int Y,
    [property: JsonPropertyName("width")] int Width,
    [property: JsonPropertyName("height")] int Height,
    [property: JsonPropertyName("maximized")] bool Maximized);

internal static class DesktopWindowStateStore
{
    private const int SchemaVersion = 1;
    private const int CoordinateLimit = 100_000;
    private const int DimensionLimit = 16_384;
    private const int MinimumVisiblePixels = 80;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
    };

    public static DesktopWindowState? TryLoad(string path, Size minimumSize)
    {
        try
        {
            if (!File.Exists(path))
            {
                return null;
            }

            var json = File.ReadAllText(path);
            var state = JsonSerializer.Deserialize<DesktopWindowState>(json, JsonOptions);
            return IsValid(state, minimumSize) ? state : null;
        }
        catch (Exception exc) when (
            exc is IOException
            or UnauthorizedAccessException
            or JsonException
            or NotSupportedException)
        {
            return null;
        }
    }

    public static bool TryApply(Form form, string path)
    {
        ArgumentNullException.ThrowIfNull(form);
        var state = TryLoad(path, form.MinimumSize);
        if (state is null)
        {
            return false;
        }

        var bounds = new Rectangle(state.X, state.Y, state.Width, state.Height);
        var workingAreas = Screen.AllScreens.Select(screen => screen.WorkingArea);
        if (!IsVisibleOnAnyScreen(bounds, workingAreas))
        {
            return false;
        }

        form.StartPosition = FormStartPosition.Manual;
        form.Bounds = bounds;
        form.WindowState = state.Maximized ? FormWindowState.Maximized : FormWindowState.Normal;
        return true;
    }

    public static bool TrySave(string path, Rectangle bounds, bool maximized, Size minimumSize)
    {
        var state = new DesktopWindowState(
            SchemaVersion,
            bounds.X,
            bounds.Y,
            bounds.Width,
            bounds.Height,
            maximized);
        if (!IsValid(state, minimumSize))
        {
            return false;
        }

        string? temporary = null;
        try
        {
            var parent = Path.GetDirectoryName(path);
            if (string.IsNullOrWhiteSpace(parent))
            {
                return false;
            }
            Directory.CreateDirectory(parent);
            temporary = path + $".tmp-{Guid.NewGuid():N}";
            File.WriteAllText(temporary, JsonSerializer.Serialize(state, JsonOptions) + Environment.NewLine);
            File.Move(temporary, path, overwrite: true);
            return true;
        }
        catch (Exception exc) when (
            exc is IOException
            or UnauthorizedAccessException
            or NotSupportedException)
        {
            return false;
        }
        finally
        {
            if (temporary is not null)
            {
                try
                {
                    File.Delete(temporary);
                }
                catch
                {
                }
            }
        }
    }

    internal static bool IsVisibleOnAnyScreen(
        Rectangle bounds,
        IEnumerable<Rectangle> workingAreas)
    {
        foreach (var area in workingAreas)
        {
            var visible = Rectangle.Intersect(bounds, area);
            if (visible.Width >= MinimumVisiblePixels && visible.Height >= MinimumVisiblePixels)
            {
                return true;
            }
        }
        return false;
    }

    private static bool IsValid(DesktopWindowState? state, Size minimumSize)
    {
        if (state is null || state.SchemaVersion != SchemaVersion)
        {
            return false;
        }
        if (Math.Abs((long)state.X) > CoordinateLimit || Math.Abs((long)state.Y) > CoordinateLimit)
        {
            return false;
        }
        if (state.Width < minimumSize.Width
            || state.Height < minimumSize.Height
            || state.Width > DimensionLimit
            || state.Height > DimensionLimit)
        {
            return false;
        }
        return true;
    }
}
