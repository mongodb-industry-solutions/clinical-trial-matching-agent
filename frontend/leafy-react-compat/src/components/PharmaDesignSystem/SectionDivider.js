import React from "react";

function SectionDivider({ label }) {
  if (!label) {
    return <hr className="pds-divider" />;
  }

  return (
    <div className="pds-divider pds-divider--labeled">
      <span className="pds-divider__line" />
      <span className="pds-divider__label">{label}</span>
      <span className="pds-divider__line" />
    </div>
  );
}

export default SectionDivider;
