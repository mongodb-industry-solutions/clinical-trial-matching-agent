import React, { useState } from 'react';

function BehindTheScenes({ title, data, defaultOpen }) {
  const [isOpen, setIsOpen] = useState(defaultOpen || false);

  if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
    return null;
  }

  return (
    <div className={`bts-container ${isOpen ? 'open' : ''}`}>
      <button
        type="button"
        className="bts-toggle"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        <span className="bts-icon">{isOpen ? '\u25BC' : '\u25B6'}</span>
        <span className="bts-label">Behind the Scenes</span>
        {title ? <span className="bts-title">{title}</span> : null}
      </button>
      {isOpen ? (
        <div className="bts-body">
          {Array.isArray(data) ? (
            data
              .filter((item) => item && item.payload && Object.keys(item.payload).length > 0)
              .map((item, idx) => (
                <details key={idx} className="bts-details">
                  <summary>{item.label || `Stage ${idx + 1}`}</summary>
                  <pre className="bts-pre">{JSON.stringify(item.payload, null, 2)}</pre>
                </details>
              ))
          ) : (
            <pre className="bts-pre">{JSON.stringify(data, null, 2)}</pre>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default BehindTheScenes;
