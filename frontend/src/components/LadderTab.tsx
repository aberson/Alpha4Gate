import { useApi } from "../hooks/useApi";
import { StaleDataBanner } from "./StaleDataBanner";

export interface LadderEntry {
  version: string;
  elo: number;
  games_played: number;
  last_updated: string;
}

export interface HeadToHeadRecord {
  wins: number;
  losses: number;
  draws: number;
}

export interface LadderData {
  standings: LadderEntry[];
  head_to_head: Record<string, Record<string, HeadToHeadRecord>>;
}

export function LadderTab() {
  const {
    data,
    isStale,
    isLoading,
    lastSuccess,
  } = useApi<LadderData>("/api/ladder", { pollMs: 10000 });

  if (!data) {
    return (
      <div>
        {isLoading
          ? "Loading..."
          : "No ladder data yet"}
      </div>
    );
  }

  const standings = data.standings;
  const h2h = data.head_to_head;
  const versions = standings.map((s) => s.version);

  return (
    <div className="ladder-tab">
      {isStale ? (
        <StaleDataBanner lastSuccess={lastSuccess} label="Ladder" />
      ) : null}
      <h2>Ladder Standings</h2>

      {standings.length === 0 ? (
        <p>No ladder data yet</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Version</th>
              <th>Elo</th>
              <th>Games Played</th>
              <th>Last Updated</th>
            </tr>
          </thead>
          <tbody>
            {standings.map((entry, i) => (
              <tr key={entry.version}>
                <td>{i + 1}</td>
                <td>{entry.version}</td>
                <td>{entry.elo}</td>
                <td>{entry.games_played}</td>
                <td>{entry.last_updated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {versions.length > 1 && Object.keys(h2h).length > 0 ? (
        <>
          <h2>Head-to-Head</h2>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th></th>
                  {versions.map((v) => (
                    <th key={v}>{v}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {versions.map((rowV) => (
                  <tr key={rowV}>
                    <td style={{ fontWeight: 600 }}>{rowV}</td>
                    {versions.map((colV) => {
                      if (rowV === colV) {
                        return (
                          <td key={colV} style={{ color: "#666" }}>
                            -
                          </td>
                        );
                      }
                      const record = h2h[rowV]?.[colV];
                      if (!record) {
                        return <td key={colV}>-</td>;
                      }
                      return (
                        <td key={colV}>
                          {record.wins}W/{record.losses}L/{record.draws}D
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}
