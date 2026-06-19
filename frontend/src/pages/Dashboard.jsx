import { useState, useEffect } from 'react';
import './Dashboard.css';

const PHASE_ORDER = ['opening', 'middlegame', 'endgame'];
const CLASS_COLORS = {
  blunder: '#e74c3c',
  mistake: '#e67e22',
  inaccuracy: '#f1c40f',
  excellent: '#2ecc71',
  good: '#27ae60',
  best: '#2980b9',
};

function StatCard({ label, value, sub, color }) {
  return (
    <div className="stat-card" style={{ borderTopColor: color || '#3498db' }}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function PhaseTable({ data }) {
  if (!data) return <p>No phase data available.</p>;
  const phases = PHASE_ORDER.filter((p) => data[p]);
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Phase</th>
          <th>Moves</th>
          <th>Blunders</th>
          <th>Blunder %</th>
          <th>Mistakes</th>
          <th>Inaccuracies</th>
        </tr>
      </thead>
      <tbody>
        {phases.map((p) => (
          <tr key={p}>
            <td className="cell-phase">{p}</td>
            <td>{data[p].total_moves}</td>
            <td style={{ color: CLASS_COLORS.blunder }}>{data[p].blunders}</td>
            <td style={{ color: CLASS_COLORS.blunder }}>{data[p].blunder_rate}%</td>
            <td style={{ color: CLASS_COLORS.mistake }}>{data[p].mistakes}</td>
            <td style={{ color: CLASS_COLORS.inaccuracy }}>{data[p].inaccuracies}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PressureTable({ data }) {
  if (!data) return <p>No time-pressure data available.</p>;
  const buckets = ['<30s', '30s–2min', '>2min'];
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Time Remaining</th>
          <th>Moves</th>
          <th>Blunders</th>
          <th>Blunder %</th>
          <th>Mistakes</th>
          <th>Inaccuracies</th>
        </tr>
      </thead>
      <tbody>
        {buckets.map((b) => (
          data[b] ? (
            <tr key={b}>
              <td className="cell-phase">{b}</td>
              <td>{data[b].total_moves}</td>
              <td style={{ color: CLASS_COLORS.blunder }}>{data[b].blunders}</td>
              <td style={{ color: CLASS_COLORS.blunder }}>{data[b].blunder_rate}%</td>
              <td style={{ color: CLASS_COLORS.mistake }}>{data[b].mistakes}</td>
              <td style={{ color: CLASS_COLORS.inaccuracy }}>{data[b].inaccuracies}</td>
            </tr>
          ) : null
        ))}
      </tbody>
    </table>
  );
}

function OpeningTable({ data }) {
  if (!data || data.length === 0) return <p>No opening data available — sync and analyze games first.</p>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>ECO</th>
          <th>Color</th>
          <th>Games</th>
          <th>Win %</th>
          <th>Accuracy %</th>
          <th>Blunder %</th>
        </tr>
      </thead>
      <tbody>
        {data.slice(0, 15).map((o) => (
          <tr key={o.eco}>
            <td className="cell-eco">{o.eco}</td>
            <td>{o.primary_color}</td>
            <td>{o.games}</td>
            <td style={{ color: o.win_rate >= 50 ? '#27ae60' : '#e74c3c' }}>{o.win_rate}%</td>
            <td>{o.avg_accuracy}%</td>
            <td style={{ color: CLASS_COLORS.blunder }}>{o.blunder_rate}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TrendTable({ data }) {
  if (!data || data.length === 0) return <p>No trend data available.</p>;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Month</th>
          <th>Games</th>
          <th>Moves</th>
          <th>Accuracy %</th>
          <th>Blunder %</th>
          <th>Avg Swing</th>
        </tr>
      </thead>
      <tbody>
        {data.map((t) => (
          <tr key={t.month}>
            <td>{t.month}</td>
            <td>{t.games}</td>
            <td>{t.total_moves}</td>
            <td style={{ color: t.accuracy >= 75 ? '#27ae60' : '#e74c3c' }}>{t.accuracy}%</td>
            <td style={{ color: CLASS_COLORS.blunder }}>{t.blunder_rate}%</td>
            <td>{t.avg_swing}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function GameList({ username }) {
  const [games, setGames] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/games/${username}?limit=20`)
      .then((r) => r.json())
      .then((data) => {
        setGames(data.games || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [username]);

  if (loading) return <p>Loading games…</p>;
  if (games.length === 0) return <p>No games found.</p>;

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>White</th>
          <th>Black</th>
          <th>Result</th>
          <th>Color</th>
          <th>Opening</th>
          <th>Moves</th>
        </tr>
      </thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.id}>
            <td>{g.date?.slice(0, 10)}</td>
            <td>{g.white}</td>
            <td>{g.black}</td>
            <td style={{ color: g.result === 'win' ? '#27ae60' : g.result === 'loss' ? '#e74c3c' : '#7f8c8d' }}>
              {g.result}
            </td>
            <td>{g.color}</td>
            <td className="cell-eco">{g.eco || '-'}</td>
            <td>{g.moves_analyzed}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Dashboard({ profileData, username }) {
  const profile = profileData?.profile;
  const noProfile = !profile || profile.total_games_analyzed === 0;

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>♟️ Shacharya</h1>
        <p className="subtitle">AI Chess Coach — {username}</p>
      </header>

      {noProfile ? (
        <div className="empty-state">
          <h2>No weakness profile yet</h2>
          <p>Run this command to generate your profile:</p>
          <code>python -m backend.cli sync-all {username}</code>
          <p className="hint">Then refresh this page.</p>
        </div>
      ) : (
        <>
          <section className="stats-grid">
            <StatCard label="Games Analyzed" value={profile.total_games_analyzed} />
            <StatCard label="Accuracy" value={`${profile.overall?.accuracy}%`} color="#27ae60" />
            <StatCard
              label="Blunder Rate"
              value={`${profile.overall?.blunder_rate}%`}
              sub={`${profile.overall?.total_blunders} total`}
              color={profile.overall?.blunder_rate > 10 ? '#e74c3c' : '#e67e22'}
            />
            <StatCard
              label="Biggest Swing"
              value={`${profile.overall?.biggest_swing}cp`}
              color="#e74c3c"
            />
            <StatCard
              label="Worst Phase"
              value={profile.overall?.most_blunder_phase}
              color="#e67e22"
            />
            <StatCard
              label="Worst Under"
              value={profile.overall?.most_blunder_pressure}
              color="#e67e22"
            />
          </section>

          <section className="section">
            <h2>Blunders by Game Phase</h2>
            <PhaseTable data={profile.by_phase} />
          </section>

          <section className="section">
            <h2>Blunders by Time Pressure</h2>
            <PressureTable data={profile.by_time_pressure} />
          </section>

          <section className="section">
            <h2>Performance by Opening</h2>
            <OpeningTable data={profile.by_opening} />
          </section>

          <section className="section">
            <h2>Monthly Trend</h2>
            <TrendTable data={profile.trend} />
          </section>

          <section className="section">
            <h2>Recent Games</h2>
            <GameList username={username} />
          </section>
        </>
      )}
    </div>
  );
}
