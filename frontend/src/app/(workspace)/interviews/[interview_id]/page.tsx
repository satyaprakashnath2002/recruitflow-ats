import { InterviewDetailPage } from "@/features/workflows/pages";
export default async function Page({params}:{params:Promise<{interview_id:string}>}){const {interview_id}=await params;return <InterviewDetailPage id={interview_id}/>;}
