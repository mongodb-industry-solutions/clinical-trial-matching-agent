import "../../components/LeafyHospitalHeader.css";
import "../../components/AtlasWorkbench.css";
import "../../leafy-react-compat/src/pages/PharmaNavigator/PharmaNavigator.css";
import "../../leafy-react-compat/src/components/PatientClinicalRecord/PatientClinicalRecord.css";
import "../../leafy-react-compat/src/components/BehindTheScenes/BehindTheScenes.css";
import "../../leafy-react-compat/src/components/PharmaDesignSystem/PharmaDesignSystem.css";
import "../../leafy-react-compat/src/components/ClinicalTrialsWorkbench/ClinicalTrialsWorkbench.css";
import "../../leafy-react-compat/src/components/ClinicalTrialCanonicalWorkspace/ClinicalTrialCanonicalWorkspace.css";
import "../../leafy-react-compat/src/components/PatientContextPicker/PatientContextPicker.css";
import "../../leafy-react-compat/src/components/PharmaStepIndicator/PharmaStepIndicator.css";
import LeafyHospitalHeader from "../../components/LeafyHospitalHeader";

export default function PharmaNavigatorLayout({ children }) {
  return (
    <div className="leafy-shell">
      <LeafyHospitalHeader />
      <div className="leafy-shell__content">{children}</div>
    </div>
  );
}
