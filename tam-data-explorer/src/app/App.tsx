import { Route, Routes } from "react-router-dom";
import { NavBar } from "./NavBar";
import { ApiAccessPage } from "./pages/ApiAccessPage";
import { BrowsePage } from "./pages/BrowsePage";
import { FileViewPage } from "./pages/FileViewPage";

export function App() {
  return (
    <>
      <NavBar current="data" />
      <Routes>
        <Route path="/" element={<BrowsePage />} />
        <Route path="/view" element={<FileViewPage />} />
        <Route path="/api-access" element={<ApiAccessPage />} />
      </Routes>
    </>
  );
}
