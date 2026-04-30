import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

/**
 * "Help" tab — renders the operator-commands cheat sheet from
 * `documentation/wiki/operator-commands.md` so the dashboard mirrors
 * the on-disk doc without duplicate maintenance.
 *
 * The backend endpoint `/api/operator-commands` reads the file on each
 * request, so an edit to the .md surfaces here on the next fetch
 * without a frontend rebuild. We don't poll — the doc rarely changes
 * and a manual page refresh is enough.
 */

interface OperatorCommandsResponse {
  markdown: string;
}

export function HelpTab() {
  const { data, isStale, isLoading, lastSuccess } = useApi<OperatorCommandsResponse>(
    "/api/operator-commands",
    { /* no pollMs — fetch once on mount */ },
  );

  if (isLoading || !data) {
    return <div>{isLoading ? "Loading help…" : "No help content available"}</div>;
  }

  return (
    <div className="help-tab">
      {isStale ? <StaleDataBanner lastSuccess={lastSuccess} label="Help" /> : null}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {data.markdown}
      </ReactMarkdown>
    </div>
  );
}
