import { useState, useEffect } from "react";

interface Condition {
  field: string;
  op: string;
  value?: number | boolean;
  value_field?: string;
}

interface RewardRule {
  id: string;
  description: string;
  condition: Condition;
  requires: Condition | null;
  reward: number;
  active: boolean;
}

interface RulesData {
  rules: RewardRule[];
}

export function RewardRuleEditor() {
  const [rules, setRules] = useState<RewardRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch("/api/reward-rules")
      .then((r) => r.json())
      .then((data: RulesData) => setRules(data.rules || []))
      .catch(() => setError("Failed to load reward rules"));
  }, []);

  const toggleActive = (id: string) => {
    setRules((prev) =>
      prev.map((r) => (r.id === id ? { ...r, active: !r.active } : r))
    );
  };

  const updateReward = (id: string, reward: number) => {
    setRules((prev) =>
      prev.map((r) => (r.id === id ? { ...r, reward } : r))
    );
  };

  const removeRule = (id: string) => {
    setRules((prev) => prev.filter((r) => r.id !== id));
  };

  const saveRules = async () => {
    setSaving(true);
    try {
      await fetch("/api/reward-rules", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rules }),
      });
      setError(null);
    } catch {
      setError("Failed to save rules");
    }
    setSaving(false);
  };

  if (error) return <div className="error">{error}</div>;

  return (
    <div className="reward-rule-editor">
      <h2>Reward Rules</h2>
      {rules.length === 0 ? (
        <div className="empty">No reward rules configured</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Description</th>
              <th>Reward</th>
              <th>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className={rule.active ? "" : "inactive"}>
                <td>{rule.id}</td>
                <td>{rule.description}</td>
                <td>
                  <input
                    type="number"
                    step="0.01"
                    value={rule.reward}
                    onChange={(e) =>
                      updateReward(rule.id, parseFloat(e.target.value) || 0)
                    }
                    style={{ width: "80px" }}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={rule.active}
                    onChange={() => toggleActive(rule.id)}
                  />
                </td>
                <td>
                  <button onClick={() => removeRule(rule.id)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button onClick={saveRules} disabled={saving}>
        {saving ? "Saving..." : "Save Rules"}
      </button>
    </div>
  );
}
