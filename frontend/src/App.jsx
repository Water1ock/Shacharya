import { useState, useEffect } from 'react';
import Dashboard from './pages/Dashboard';

export default function App() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [username] = useState('wa7erlock');

  useEffect(() => {
    fetch(`/api/profile/${username}`)
      .then((r) => r.json())
      .then((data) => {
        setProfile(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [username]);

  if (loading) {
    return (
      <div className="app-loading">
        <div className="spinner" />
        <p>Loading your Shacharya profile…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="app-error">
        <h2>Could not connect to backend</h2>
        <p>{error}</p>
        <p className="hint">
          Make sure the backend is running:{' '}
          <code>venv/Scripts/uvicorn backend.app.main:app --reload</code>
        </p>
      </div>
    );
  }

  return <Dashboard profileData={profile} username={username} />;
}
