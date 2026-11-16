import { CandidateDetailPage } from "@/features/workflows/pages";
export default async function Page({params}:{params:Promise<{candidate_id:string}>}){const {candidate_id}=await params;return <CandidateDetailPage id={candidate_id}/>;}
