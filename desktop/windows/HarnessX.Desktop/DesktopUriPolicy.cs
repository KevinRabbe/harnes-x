namespace HarnessX.Desktop;

internal static class DesktopUriPolicy
{
    public static bool IsAllowedNavigation(string value, Uri origin)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var candidate))
        {
            return false;
        }
        return candidate.Scheme == Uri.UriSchemeHttp
            && string.Equals(candidate.Scheme, origin.Scheme, StringComparison.OrdinalIgnoreCase)
            && string.Equals(candidate.Host, origin.Host, StringComparison.Ordinal)
            && candidate.Port == origin.Port
            && string.IsNullOrEmpty(candidate.UserInfo);
    }
}
